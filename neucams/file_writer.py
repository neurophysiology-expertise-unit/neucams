# Classes to save files from a multiprocessing queue
import time
import sys
import os
from os.path import join, isfile, dirname, splitext
from multiprocessing import Process, Queue, Event, Array
import queue
from datetime import datetime
import numpy as np
from tifffile import TiffWriter as twriter
from skvideo.io import FFmpegWriter
import cv2
from neucams.utils import display
from multiprocessing import shared_memory

VERSION = 'B0.8'


def shm_frame(shm_name, shape, dtype):
    shm = shared_memory.SharedMemory(name=shm_name)
    arr = np.ndarray(shape, dtype=np.dtype(dtype), buffer=shm.buf)
    return arr, shm


def _attach_shm_with_retry(shm_name, shape, dtype, retries=3, delay=0.002):
    """Attach to an existing SharedMemory by name with a couple of short retries."""
    for i in range(retries):
        try:
            shm = shared_memory.SharedMemory(name=shm_name)
            arr = np.ndarray(shape, dtype=np.dtype(dtype), buffer=shm.buf)
            return arr, shm
        except FileNotFoundError:
            if i == retries - 1:
                raise
            time.sleep(delay)


class FileWriter(Process):
    """Abstract class to write to file(s)
    Runs in a separate process
    Final naming is tied to the TXT timelog:
      {YYMMDD_HHMMSSmmm_0000X}.{ext}
    Rollover (frames_per_file>0): ..._00001.ext -> ..._00002.ext -> ...
    """
    sleeptime = 0.05
    queue_timeout = 0.05

    def __init__(self, filepath, extension="log", frames_per_file=0):
        super().__init__()
        self.filepath_array = Array('u', ' ' * 1024)

        # Naming / rollover state
        self.extension = extension
        self.base_filepath = filepath            # user-provided base (folder or file). Will be replaced by timelog base.
        self._ts_run_prefix = None               # e.g., "250926_121656673"
        self._run_file_index = None              # 5-digit index chosen with TXT

        # Runtime flags/queues
        self.frames_per_file = frames_per_file
        self.start_flag = Event()
        self.stop_flag = Event()
        self.close_flag = Event()
        self.is_run_closed = Event()
        self.inQ = Queue()

        # Handlers/state
        self.file_handler = None
        self.ts_file_handler = None

        # Timestamp normalization
        self.master_t0_ns = None
        self._ts_offset = None
        self._ts_scale = None    # chosen from {1, 1e3, 1e6, 1e9} to convert to seconds
        self._ts_deltas = []     # collect a few deltas to infer scale robustly
        self._ts_buffer = []     # early-line buffer until scale is known
        self._ts_ready = False

        # Counters / meta
        self.global_frame_index = 0
        self.file_frame_index = 0
        self._cam_info = {}      # Store camera information for pre-initialization

        self.error_count = 0
        self.start()
        self.start_flag.wait()  # do not return handle before process started

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        self.join()

    def set_master_t0_ns(self, t0_ns: int):
        self.master_t0_ns = int(t0_ns)
    # ---------- Path API ----------

    def get_filepath(self):
        """Access the data filepath outside of process."""
        return str(self.filepath_array[:]).strip(' ')

    def set_filepath(self, filepath):
        """Accept either a FOLDER path or a FILE path. Create folders as needed."""
        # Ensure we can switch safely
        if not self.stop_flag.is_set():
            display("ERROR: Cannot change filepath while acquisition is active. Please stop the camera first.", level='error')
            return

        # Close any open file/timelog cleanly (old run)
        self._release_file_handler()
        self._release_ts_log()

        # Treat plain folder paths correctly
        is_dir_like = os.path.isdir(filepath) or filepath.endswith(os.sep)
        name = os.path.basename(filepath)
        name_noext, ext = os.path.splitext(name)
        if is_dir_like or (name and not ext and not os.path.isfile(filepath)):
            # Folder-like input (existing dir OR looks like a folder name)
            user_folder = filepath.rstrip(os.sep)
            self.base_filepath = user_folder  # temporarily store folder; _open_ts_log will set run basename
            folder = user_folder
        else:
            # Looks like a file path; we'll still unify to timelog later
            self.base_filepath = filepath
            folder = dirname(filepath) or '.'

        # Ensure folder exists
        if folder and not os.path.exists(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                display(f"Could not create folder {folder} : {e}", level='error')

        # Fresh timelog for the new run (even before frames arrive)
        self._open_ts_log()

        self.file_handler = None

        # Reset counters for the new run
        self.global_frame_index = 0
        self.file_frame_index = 0
        if hasattr(self, 'saved_frame_count'):
            self.saved_frame_count = 0

        self.is_run_closed.clear()

        # Optional: pre-open handler if camera info known (keeps path consistent)
        if self._cam_info:
            self._init_file_handler(frame=None)

    def set_cam_info(self, **kwargs):
        """Set camera information for pre-initialization of file handlers."""
        self._cam_info.update(kwargs)

    # ---------- Path helpers ----------

    def get_complete_filepath(self, filepath):
        """Adds the extension, checks that the filepath is available.
        If exists, appends _1, _2, ... (legacy helper; avoid for first-of-run)."""
        complete_filepath = f"{filepath}.{self.extension}"
        if not isfile(complete_filepath):
            return complete_filepath
        i = 1
        complete_filepath = f"{filepath}_{i}.{self.extension}"
        while isfile(complete_filepath):
            i += 1
            complete_filepath = f"{filepath}_{i}.{self.extension}"
        return complete_filepath

    def get_complete_filepath_exact(self, filepath):
        """Return filepath with extension, without adding _1, _2…"""
        return f"{filepath}.{self.extension}"

    def update_filepath_array(self, filepath):
        # publish to shared array
        for i in range(len(self.filepath_array)):
            self.filepath_array[i] = ' '
        for i in range(min(len(filepath), len(self.filepath_array))):
            self.filepath_array[i] = filepath[i]

    # ---------- Timelog / timestamps ----------

    # at the very top of file_writer.py (module level — keep it here, not inside a function)


    def _open_ts_log(self):
        """
        Open a timelog named as YYMMDD_HHMMSSmmm_0000X.txt next to the data files.
        Also publish that exact basename to the data path so TIFFs and timelog match.
        Never crash the writer if anything goes wrong.
        """
        try:
            # Resolve the folder from base_filepath (folder or file-base)
            base = getattr(self, 'base_filepath', '') or ''
            if base and (os.path.isdir(base) or base.endswith(os.sep)):
                folder = base.rstrip(os.sep)
            else:
                folder = dirname(base) or '.'
            os.makedirs(folder, exist_ok=True)

            # Build (or reuse) run prefix, e.g. "250930_161650123"
            if not getattr(self, '_ts_run_prefix', None):
                now = datetime.now()
                self._ts_run_prefix = now.strftime("%y%m%d_%H%M%S") + f"{now.microsecond // 1000:03d}"

            # Find next free 5-digit index → "..._00001.txt"
            i = 1
            while True:
                candidate = f"{self._ts_run_prefix}_{i:05d}.txt"
                ts_path = join(folder, candidate)
                if not os.path.exists(ts_path):
                    break
                i += 1

            # Open the timelog FIRST, then write the header/metadata
            self.ts_file_handler = open(ts_path, 'w', encoding='utf-8')
            self.ts_file_handler.write('# frame_index\ttimestamp_seconds\n')
            # Optional: write master t0 if present (does not re-import datetime)
            if getattr(self, 'master_t0_ns', None) is not None:
                t0_ns = float(self.master_t0_ns)
                self.ts_file_handler.write(f'# master_t0_ns\t{int(t0_ns)}\n')
                self.ts_file_handler.write(f'# master_t0_iso\t{datetime.fromtimestamp(t0_ns / 1e9).isoformat()}\n')
            self.ts_file_handler.flush()

            # Publish the exact basename so data files match the timelog prefix
            run_basename = os.path.splitext(os.path.basename(ts_path))[0]
            self._run_file_index = i
            data_base = join(folder, run_basename)
            self.base_filepath = data_base
            self.update_filepath_array(self.get_complete_filepath_exact(data_base))

            # Reset timestamp state for this run
            self._ts_offset = None
            self._ts_scale = None
            self._ts_deltas = []
            self._ts_buffer = []
            self._ts_ready = False
            self.global_frame_index = 0

        except Exception as e:
            display(f"Failed to open timestamp log in {folder}: {e}", level='warning')
            self.ts_file_handler = None

    def _init_file_handler(self, frame=None):
        """Open the data file for the current run; precreates folders; tolerates timelog failure."""
        # If a frame is provided, update cam_info (shape/dtype/channels)
        if frame is not None:
            if hasattr(frame, 'shape'):
                self._cam_info['shape'] = frame.shape
            if hasattr(frame, 'dtype'):
                self._cam_info['dtype'] = frame.dtype
            if hasattr(frame, 'ndim'):
                self._cam_info['ndim'] = frame.ndim
            if hasattr(frame, 'n_channels'):
                self._cam_info['n_channels'] = frame.n_channels
            elif frame.ndim == 3:
                self._cam_info['n_channels'] = frame.shape[2]
            else:
                self._cam_info['n_channels'] = 1

        # Rollover (per-file frame limit)
        if (self.frames_per_file > 0 and
            getattr(self, 'saved_frame_count', 0) > 0 and
            (self.saved_frame_count % self.frames_per_file) == 0):
            if hasattr(self, 'base_filepath'):
                new_path = self.get_complete_filepath(self.base_filepath)
                self.update_filepath_array(new_path)

        # Ensure parent folder exists
        current_filepath = self.get_filepath()
        folder = dirname(current_filepath) or '.'
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as me:
            display(f"Could not create folder {folder}: {me}", level='error')

        # Open / reuse the timelog, but NEVER die if this fails
        if self.ts_file_handler is None:
            try:
                self._open_ts_log()
            except Exception as e:
                display(f"Timelog open raised unexpectedly: {e}", level='warning')
                self.ts_file_handler = None

        # Close previous data file (NOT the timelog) and open a fresh one
        self._release_file_handler()
        display(f"Opening: {current_filepath}")  # visible confirmation in console
        self.file_handler = self._get_file_handler(
            current_filepath, cam_info=self._cam_info, frame=frame
        )


    def _release_ts_log(self):
        if self.ts_file_handler is not None:
            try:
                self.ts_file_handler.flush()
                self.ts_file_handler.close()
            except Exception:
                pass
            finally:
                self.ts_file_handler = None

    def _infer_scale_from_deltas(self):
        if not self._ts_deltas:
            return None
        med = float(np.median(self._ts_deltas))
        # Heuristics based on typical camera timestamp units per frame interval
        # ~0.033 -> seconds, ~33 -> ms, ~33,000 -> us, ~33,000,000 -> ns
        if med > 1e6:
            return 1e9   # nanoseconds
        if med > 1e3:
            return 1e6   # microseconds
        if med > 1.0:
            return 1e3   # milliseconds
        return 1.0       # seconds

    def _write_timestamp_line(self, frame_idx, ts_raw):
        """
        Buffer early timestamps until we can infer scale, then flush.
        Ensures time starts at 0.0000 and no giant values sneak in.
        """
        # Establish zero on first sample
        if getattr(self, "_ts_offset", None) is None:
            self._ts_offset = ts_raw

        # Collect deltas to infer units
        if ts_raw is not None and self._ts_offset is not None:
            d = abs(ts_raw - self._ts_offset)
            if d > 0:
                self._ts_deltas.append(d)

        # Decide scale once we have a few deltas
        # Decide scale once we have a few deltas
        if self._ts_scale is None and len(self._ts_deltas) >= 3:
            med = float(np.median(self._ts_deltas[-10:]))
            if med > 1e6:
                self._ts_scale = 1e9   # raw deltas ~ tens of millions → ns
            elif med > 1e3:
                self._ts_scale = 1e6   # raw deltas ~ tens of thousands → µs
            elif med > 1.0:
                self._ts_scale = 1e3   # raw deltas ~ tens → ms
            else:
                self._ts_scale = 1.0   # raw deltas already in seconds (Hamamatsu case)


        # If still unknown, buffer and return
        if self._ts_scale is None:
            self._ts_buffer.append((frame_idx, ts_raw))
            return

        # First known scale → flush buffer
        if not self._ts_ready and self._ts_buffer:
            for fi, tr in self._ts_buffer:
                tsec = max(0.0, (tr - self._ts_offset) / self._ts_scale)
                self.ts_file_handler.write(f"{fi}\t{tsec:.4f}\n")
            self._ts_buffer.clear()
            self._ts_ready = True

        # Write current line
        tsec = max(0.0, (ts_raw - self._ts_offset) / self._ts_scale)
        self.ts_file_handler.write(f"{frame_idx}\t{tsec:.4f}\n")

    def _log_timestamp(self, timestamp):
        """Write 'index<TAB>seconds' with 4 decimals, infer unit scale automatically."""
        if self.ts_file_handler is None:
            return
        try:
            ts_raw = float(timestamp)
            if self._ts_offset is None:
                self._ts_offset = ts_raw
            self._write_timestamp_line(self.global_frame_index, ts_raw)
            if (self.global_frame_index & 63) == 0:
                self.ts_file_handler.flush()
        except Exception as e:
            display(f"Failed writing timestamp for {self.get_filepath()}: {e}", level='warning')
        finally:
            self.global_frame_index += 1

    # ---------- Open/rollover data files ----------

    def _init_file_handler(self, frame=None):
        """Open data file. Prioritizes pre-set camera info, falls back to frame if provided."""

        # Ensure the TXT is open so the timestamped run basename exists
        if self.ts_file_handler is None:
            self._open_ts_log()

        # Align data base with TXT basename (belt-and-suspenders)
        ts_path = getattr(self.ts_file_handler, 'name', None)
        if ts_path:
            folder = dirname(ts_path) or '.'
            run_basename = os.path.splitext(os.path.basename(ts_path))[0]
            candidate_base = join(folder, run_basename)
            # Remember prefix/index for rollover
            try:
                parts = run_basename.split('_')
                self._run_file_index = int(parts[-1])
                self._ts_run_prefix = '_'.join(parts[:-1])
            except Exception:
                pass
            if getattr(self, 'base_filepath', '') != candidate_base:
                self.base_filepath = candidate_base
                self.update_filepath_array(self.get_complete_filepath_exact(candidate_base))

        # If frame provided, cache cam info (shape/dtype/channels) for writer creation
        if frame is not None:
            if hasattr(frame, 'shape'):
                self._cam_info['shape'] = frame.shape
            if hasattr(frame, 'dtype'):
                self._cam_info['dtype'] = frame.dtype
            if hasattr(frame, 'ndim'):
                self._cam_info['ndim'] = frame.ndim
            if hasattr(frame, 'n_channels'):
                self._cam_info['n_channels'] = frame.n_channels
            elif frame.ndim == 3:
                self._cam_info['n_channels'] = frame.shape[2]
            else:
                self._cam_info['n_channels'] = 1

        # Rollover by incrementing the 5-digit suffix
        if (self.frames_per_file > 0 and
            getattr(self, 'saved_frame_count', 0) > 0 and
            (self.saved_frame_count % self.frames_per_file) == 0):

            folder = dirname(self.get_filepath()) or '.'
            prefix = self._ts_run_prefix or os.path.basename(self.base_filepath).rsplit('_', 1)[0]
            idx = (getattr(self, "_run_file_index", 1) or 1) + 1
            while True:
                candidate_base = join(folder, f"{prefix}_{idx:05d}")
                candidate_path = self.get_complete_filepath_exact(candidate_base)
                if not os.path.exists(candidate_path):
                    break
                idx += 1
            self._run_file_index = idx
            self.base_filepath = candidate_base
            self.update_filepath_array(self.get_complete_filepath_exact(candidate_base))

        # Use the shared/published path to open writer
        current_filepath = self.get_filepath()
        folder = dirname(current_filepath) or '.'
        if not os.path.exists(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                display(f"Could not create folder {folder} : {e}", level='error')

        # Close previous data file only (keep timelog)
        self._release_file_handler()

        # Open new data file handler
        self.file_handler = self._get_file_handler(current_filepath, cam_info=self._cam_info, frame=frame)

    def _get_file_handler(self, filepath, cam_info=None, frame=None):
        """get specific file handler (override in subclasses)"""
        raise NotImplementedError

    def _release_file_handler(self):
        """close specific data file handler (not the timelog)"""
        if self.file_handler is not None:
            try:
                self.file_handler.close()
            except Exception:
                pass
            finally:
                self.file_handler = None

    # ---------- Queue / loop ----------

    def _clear_queue(self):
        try:
            while True:
                self.inQ.get_nowait()
        except queue.Empty:
            pass

    def _write(self, frame, frameid, timestamp):
        """write specific (override in subclasses)"""
        raise NotImplementedError

    def save(self, frame, metadata):
        try:
            self.inQ.put((frame, metadata), timeout=self.queue_timeout)
        except queue.Full:
            print("ERROR: could not save image, queue is full")

    def run(self):
        self.start_flag.set()
        while not self.close_flag.is_set():
            self.saved_frame_count = 0
            self.error_count = 0
            while not self.stop_flag.is_set():
                time.sleep(self.sleeptime)
                self._process_queue()
            self._close_run()

    def _close_run(self):
        self._process_queue()
        self._release_file_handler()
        self._release_ts_log()  # single timelog per run
        self._clear_queue()
        self.stop_flag.clear()
        self.is_run_closed.set()
        self._ts_run_prefix = None  # new runs get a fresh prefix

    def _process_queue(self):
        while True:
            try:
                self._save_next_in_queue()
            except queue.Empty:
                break

    def _save_next_in_queue(self):
        buff = self.inQ.get(timeout=self.queue_timeout)
        self._handle_frame(buff)

    def _handle_frame(self, buff):
        frame, metadata = buff

        # SHM tuple (name, shape, dtype)
        if isinstance(frame, tuple) and len(frame) == 3 and isinstance(frame[0], str):
            shm_name, shape, dtype = frame
            try:
                frame, shm = _attach_shm_with_retry(shm_name, shape, dtype)
            except FileNotFoundError:
                self.error_count += 1
                return
            try:
                if (self.file_handler is None or
                    (self.frames_per_file > 0 and np.mod(self.saved_frame_count, self.frames_per_file) == 0)):
                    self._init_file_handler(frame)
                frameid, timestamp = metadata[:2]
                try:
                    self._write(frame, frameid, timestamp)
                    self._log_timestamp(timestamp)  # log after successful write
                    self.saved_frame_count += 1
                except Exception:
                    self.error_count += 1
            finally:
                shm.close()
                shm.unlink()

        else:
            if (self.file_handler is None or
                (self.frames_per_file > 0 and np.mod(self.saved_frame_count, self.frames_per_file) == 0)):
                self._init_file_handler(frame)
            frameid, timestamp = metadata[:2]
            try:
                self._write(frame, frameid, timestamp)
                self._log_timestamp(timestamp)  # log after successful write
                self.saved_frame_count += 1
            except Exception:
                self.error_count += 1

    def close(self):
        self.close_flag.set()
        self.stop_flag.set()


# ---------------- Specific writers ----------------

class TiffWriter(FileWriter):
    def __init__(self, filepath, frames_per_file=256, compression=None):
        self.compression = None
        if compression is not None:
            if compression > 9:
                display('Can not use compression over 9 for the TiffWriter')
            elif compression > 0:
                self.compression = compression
        super().__init__(filepath, extension='tif', frames_per_file=frames_per_file)

    def _get_file_handler(self, filepath, cam_info=None, frame=None):
        display('Opening: ' + filepath)
        return twriter(filepath)

    def _write(self, frame, frameid, timestamp):
        self.file_handler.save(
            frame,
            compress=self.compression,
            description='id:{0};timestamp:{1}'.format(frameid, timestamp)
        )


class BinaryWriter(FileWriter):
    def __init__(self, filepath, frames_per_file=0, **kwargs):
        super().__init__(filepath=filepath + "_{n_chan}_{H}_{W}_{dtype}",
                         frames_per_file=frames_per_file,
                         extension='dat')

    def _get_file_handler(self, filepath, cam_info=None, frame=None):
        # Prioritize cam_info if available
        if cam_info and 'dtype' in cam_info and 'shape' in cam_info and 'n_channels' in cam_info:
            dtype = cam_info['dtype']
            shape = cam_info['shape']
            n_chan = cam_info['n_channels']
        elif frame is not None:
            dtype = frame.dtype
            shape = frame.shape
            n_chan = frame.shape[2] if frame.ndim == 3 else 1
        else:
            raise ValueError("[BinaryWriter] Need frame or cam_info to open a file.")

        if dtype == np.float32:
            dtype_str = 'float32'
        elif dtype == np.uint8:
            dtype_str = 'uint8'
        else:
            dtype_str = 'uint16'

        filepath = filepath.format(n_chan=n_chan, W=shape[1], H=shape[0], dtype=dtype_str)
        display('Opening: ' + filepath)
        return open(filepath, 'wb')

    def _write(self, frame, frameid, timestamp):
        if isinstance(frame, np.ndarray):
            self.file_handler.write(frame.tobytes(order='C'))
        else:
            self.file_handler.write(frame)
        if np.mod(frameid, 5000) == 0:
            display('Wrote frame id - {0}'.format(frameid))


class FFMPEGWriter(FileWriter):
    def __init__(self, filepath, frames_per_file=0, hwaccel=None, frame_rate=None, compression=17, **kwargs):
        super().__init__(filepath, frames_per_file=frames_per_file, extension='avi')
        self.compression = compression
        if frame_rate is None:
            frame_rate = 0
        if frame_rate <= 0:
            frame_rate = 30.
        self.frame_rate = frame_rate
        if hwaccel is None:
            self.doutputs = {'-format': 'h264',
                             '-pix_fmt': 'gray',
                             '-vcodec': 'libx264',
                             '-threads': str(10),
                             '-crf': str(self.compression)}
        else:
            if hwaccel == 'intel':
                if self.compression == 0:
                    display('Using compression 17 for the intel Media SDK encoder')
                    self.compression = 17
                self.doutputs = {'-format': 'h264',
                                 '-pix_fmt': 'yuv420p',
                                 '-vcodec': 'h264_qsv',
                                 '-global_quality': str(25),
                                 '-look_ahead': str(1),
                                 '-threads': str(1),
                                 '-crf': str(self.compression)}
            elif hwaccel == 'nvidia':
                if self.compression == 0:
                    display('Using compression 25 for the NVIDIA encoder')
                    self.compression = 25
                self.doutputs = {'-vcodec': 'h264_nvenc',
                                 '-pix_fmt': 'yuv420p',
                                 '-cq:v': str(self.compression),
                                 '-threads': str(1),
                                 '-preset': 'medium'}
        self.hwaccel = hwaccel

    def set_video_settings(self, cam):
        """Sets camera specific variables - happens after camera load"""
        self.frame_rate = None
        if hasattr(cam, 'frame_rate'):
            self.frame_rate = cam.frame_rate
        self.nchannels = 1
        if hasattr(cam, 'nchan'):
            self.nchannels = cam.nchan

    def _get_file_handler(self, filepath, cam_info=None, frame=None):
        # Prioritize cam_info if available
        if cam_info and 'frame_rate' in cam_info and 'shape' in cam_info:
            frame_rate = cam_info['frame_rate']
            shape = cam_info['shape']
            dtype = cam_info['dtype']
            n_channels = cam_info['n_channels']
        elif frame is not None:
            frame_rate = self.frame_rate
            shape = frame.shape
            dtype = frame.dtype
            n_channels = frame.shape[2] if frame.ndim == 3 else 1
        else:
            raise ValueError('[FFMPEGWriter] Need frame or cam_info to open a file.')

        if frame_rate is None or frame_rate <= 0:
            display('Using 30Hz frame rate for ffmpeg')
            frame_rate = 30

        self.doutputs['-r'] = str(frame_rate)
        self.dinputs = {'-r': str(frame_rate)}

        # Lossless 16-bit path
        if dtype in [np.uint16] and n_channels == 1:
            filepath = filepath.rsplit(".", 1)[0] + '.mov'
            inputdict = {'-pix_fmt': 'gray16le', '-r': str(frame_rate)}
            outputdict = {'-c:v': 'libopenjpeg', '-pix_fmt': 'gray16le', '-r': str(frame_rate)}
        else:
            inputdict = self.dinputs
            outputdict = self.doutputs

        display('Opening: ' + filepath)
        return FFmpegWriter(filepath, inputdict=inputdict, outputdict=outputdict)

    def _write(self, frame, frameid, timestamp):
        self.file_handler.writeFrame(frame)


class OpenCVWriter(FileWriter):
    def __init__(self, filepath, frames_per_file=0, fourcc='XVID', frame_rate=60, **kwargs):
        self.frame_rate = frame_rate if frame_rate and frame_rate > 0 else 20
        cv2.setNumThreads(6)
        self.fourcc = cv2.VideoWriter_fourcc(*fourcc)
        self.w = None
        self.h = None
        super().__init__(filepath, extension='avi', frames_per_file=frames_per_file)

    def _release_file_handler(self):
        if self.file_handler is not None:
            try:
                self.file_handler.release()
            except Exception:
                pass
            finally:
                self.file_handler = None

    def _get_file_handler(self, filepath, cam_info=None, frame=None):
        # Prioritize cam_info if available
        if cam_info and 'shape' in cam_info and 'frame_rate' in cam_info and 'n_channels' in cam_info:
            w = cam_info['shape'][1]
            h = cam_info['shape'][0]
            is_color = cam_info['n_channels'] == 3
            frame_rate = cam_info['frame_rate']
        elif frame is not None:
            w = frame.shape[1]
            h = frame.shape[0]
            is_color = frame.ndim == 3 and frame.shape[2] == 3
            frame_rate = self.frame_rate
        else:
            raise ValueError('[OpenCVWriter] Need frame or cam_info to open a file.')

        # Cache WxH
        if self.w is None or self.w == 0:
            self.w = w
        if self.h is None or self.h == 0:
            self.h = h

        if not frame_rate or frame_rate < 1:
            frame_rate = 30

        display('Opening: ' + filepath)
        writer = cv2.VideoWriter(filepath, self.fourcc, frame_rate, (self.w, self.h), is_color)
        if not writer.isOpened():
            display(f"OpenCV VideoWriter failed to open for {filepath} with fourcc={self.fourcc}. Trying MJPG...", level='warning')
            mjpg = cv2.VideoWriter_fourcc(*'MJPG')
            writer = cv2.VideoWriter(filepath, mjpg, frame_rate, (self.w, self.h), is_color)
            if not writer.isOpened():
                display(f"OpenCV VideoWriter still failed to open for {filepath}.", level='error')
        return writer

    def _write(self, frame, frameid, timestamp):
        img = frame
        # Squeeze singleton channel dimension for grayscale
        if img.ndim == 3 and img.shape[2] == 1:
            img = img[:, :, 0]
        # Convert to 8-bit for OpenCV VideoWriter if needed
        if img.dtype == np.uint16:
            img = (img >> 8).astype(np.uint8)
        elif img.dtype == np.float32 or img.dtype == np.float64:
            img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
        # Ensure proper shape
        if img.ndim == 3 and img.shape[2] not in (1, 3):
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.shape[2] > 1 else img
        self.file_handler.write(img)
