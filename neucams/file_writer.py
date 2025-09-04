# Classes to save files from a multiprocessing queue
import time
import sys
import os
from os.path import join, isfile, dirname, splitext
from multiprocessing import Process, Queue, Event, Array, Value
import queue
from datetime import datetime
import numpy as np
from tifffile import imread, TiffFile, TiffWriter as twriter
from skvideo.io import FFmpegWriter
import cv2
from neucams.utils import display
from multiprocessing import shared_memory

VERSION = 'B0.6'


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
    Takes a filepath, an extension and an optional frames_per_file (default is unlimited)
    Final format is: 
    {filepath}.extension if that file not already present
    otherwise {filepath}_i.extension where i is the first index available in the folder (does not overwrite)
    """
    sleeptime = 0.05
    queue_timeout = 0.05
    
    def __init__(self, filepath,
                       extension = "log",
                       frames_per_file = 0):
        super().__init__()
        self.filepath_array = Array('u',' ' * 1024)
        # Set extension first (needed for get_complete_filepath)
        self.extension = extension
        # Store the base path for rollover operations
        self.base_filepath = filepath
        # Initialize with complete filepath (with extension and availability check)
        complete_filepath = self.get_complete_filepath(filepath)
        self.update_filepath_array(complete_filepath)
        # Don't store separate copies - always use the shared array
        self.frames_per_file = frames_per_file

        self.start_flag = Event()
        self.stop_flag  = Event()
        self.close_flag = Event()
        
        self.is_run_closed = Event()
        
        self.inQ = Queue()

        self.file_handler = None
        self._ts_offset = None
        self.ts_file_handler = None
        self.global_frame_index = 0
        self._ts_scale = None  # chosen from {1, 1e3, 1e6, 1e9} to convert to seconds
        self._ts_deltas = []   # collect a few deltas to infer scale robustly


        self.file_frame_index = 0  # 0..(frames_per_file-1) within current file

        self.error_count = 0
        self.start()
        self.start_flag.wait() #do not return handle before process started

    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        self.join()
    
    def get_filepath(self):
        """To access filepath outside of process
        """
        return str(self.filepath_array[:]).strip(' ')
        
    def set_filepath(self, filepath):
        if self.start_flag.is_set():
            self.stop_flag.set()
            self.is_run_closed.wait()
            self.is_run_closed.clear()
        
        # Store the base filepath for rollover operations
        self.base_filepath = filepath
        # Update the shared array with the complete filepath
        complete_filepath = self.get_complete_filepath(filepath)
        self.update_filepath_array(complete_filepath)
        self.file_handler = None
        
        # Reset frame counting for new file/run and log the reset
        old_global_count = self.global_frame_index
        self.global_frame_index = 0
        self.file_frame_index = 0
        if hasattr(self, 'saved_frame_count'):
            self.saved_frame_count = 0
        
        if old_global_count > 0:
            display(f"Frame count reset: {old_global_count} frames reset to 0 for new save location: {complete_filepath}")
        
    def get_complete_filepath(self, filepath):
        """Adds the extension, checks that the filepath is available.
        If not, checks the next available filepath using an unpadded index:
            filepath_1.extension, filepath_2.extension, ...
        """
        # First try without suffix
        complete_filepath = f"{filepath}.{self.extension}"
        if not isfile(complete_filepath):
            return complete_filepath
        
        # If base name exists, try with incrementing suffix
        i = 1
        complete_filepath = f"{filepath}_{i}.{self.extension}"
        while isfile(complete_filepath):
            i += 1
            complete_filepath = f"{filepath}_{i}.{self.extension}"
        return complete_filepath
                                                                        
    def update_filepath_array(self, filepath):
        for i in range(len(self.filepath_array)):
            self.filepath_array[i] = ' '
        for i in range(len(filepath)):
            self.filepath_array[i] = filepath[i]

    # --- Timestamp sidecar helpers (single file per run) ---
    def _open_ts_log(self):
        """Open a single timelog.txt in the target folder (overwrite each run)."""
        try:
            folder = dirname(self.get_filepath()) or '.'
            ts_path = join(folder, 'timelog.txt')  # single fixed name
            self.ts_file_handler = open(ts_path, 'w', encoding='utf-8')
            self.ts_file_handler.write('# frame_index\ttimestamp_seconds\n')
            self.ts_file_handler.flush()
            # Reset run-wide state
            self._ts_offset = None
            self._ts_scale = None
            self._ts_deltas = []
            self.global_frame_index = 0
        except Exception as e:
            display(f"Failed to open timestamp log in {folder}: {e}", level='warning')
            self.ts_file_handler = None

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
        # Use median of collected deltas for robustness
        med = float(np.median(self._ts_deltas))
        # Heuristics based on typical camera timestamp units per frame interval
        # ~0.033 -> seconds, ~33 -> milliseconds, ~33000 -> microseconds, ~33,000,000 -> nanoseconds
        if med > 1e6:
            return 1e9   # nanoseconds
        if med > 1e3:
            return 1e6   # microseconds
        if med > 1.0:
            return 1e3   # milliseconds
        return 1.0       # seconds

    def _log_timestamp(self, timestamp):
        """Write 'index<TAB>seconds' with 4 decimals, infer unit scale automatically."""
        if self.ts_file_handler is None:
            return
        try:
            ts = float(timestamp)

            # Establish zero at first sample
            if self._ts_offset is None:
                self._ts_offset = ts

            rel_raw = ts - self._ts_offset  # raw units

            # Collect a few deltas to infer proper scale
            if self.global_frame_index > 0:
                self._ts_deltas.append(rel_raw)
                if len(self._ts_deltas) >= 3 and self._ts_scale is None:
                    self._ts_scale = self._infer_scale_from_deltas()
                    if self._ts_scale is None:
                        self._ts_scale = 1.0

            scale = self._ts_scale or 1.0  # default to seconds until inferred
            # If scale looks wrong (e.g., seconds but deltas are huge), try a fallback
            if scale == 1.0 and len(self._ts_deltas) >= 3:
                # If seconds produce numbers that are implausibly large, try microseconds
                if np.median(self._ts_deltas) > 10.0:
                    scale = 1e3
            rel_sec = rel_raw / scale

            # Write line: index<TAB>seconds(4dp)
            self.ts_file_handler.write(f"{self.global_frame_index}\t{rel_sec:.4f}\n")
            if (self.global_frame_index & 63) == 0:
                self.ts_file_handler.flush()
        except Exception as e:
            display(f"Failed writing timestamp for {self.filepath}: {e}", level='warning')
        finally:
            self.global_frame_index += 1


    def _init_file_handler(self, frame):
        """open file generic"""
        # For rollover, generate a new complete path based on the current base
        if (self.frames_per_file > 0 and
            getattr(self, 'saved_frame_count', 0) > 0 and
            (self.saved_frame_count % self.frames_per_file) == 0):
            # Rollover: pick next available based on the current base filepath
            if hasattr(self, 'base_filepath'):
                new_path = self.get_complete_filepath(self.base_filepath)
                self.update_filepath_array(new_path)

        # Use whatever is currently in the shared array as the target path
        current_filepath = self.get_filepath()
        folder = dirname(current_filepath)
        if not os.path.exists(folder):
            try:
                os.makedirs(folder)
            except Exception as e:
                print(f"Could not create folder {folder} : {e}")
        
        # Open timestamp log on first frame (after directory is created)
        if self.saved_frame_count == 0 and self.ts_file_handler is None:
            self._open_ts_log()
            
        # Close previous file **only** (keep timelog open across rollovers)
        self._release_file_handler()
        # Open new file handler
        self.file_handler = self._get_file_handler(current_filepath, frame)

        
    def _get_file_handler(self, filepath, frame):
        """get specific file handler"""
        pass
        
    def _release_file_handler(self):
        """close specific file handler (not the timelog)"""
        if self.file_handler is not None:
            try:
                self.file_handler.close()
            except Exception:
                pass
            finally:
                self.file_handler = None


    def _clear_queue(self):
        try:
            while True:
                self.inQ.get_nowait()
        except queue.Empty:
            pass
    

    def _write(self,frame,frameid,timestamp):
        """write specific"""
        pass

    def save(self,frame,metadata):
        try:
            # QUEUE DEBUG
            import numpy as np
            # Remove type/shape debug prints
            self.inQ.put((frame,metadata), timeout = self.queue_timeout)
        except queue.Full:
            print("ERROR: could not save image, queue is full")
    
    def run(self):
        # DO NOT reset filepath here - it should only be set by external calls to set_filepath()
        # self.set_filepath(self.filepath)  # <-- THIS WAS THE BUG
        self.start_flag.set()
        while not self.close_flag.is_set():
            # Start of a run
            self.saved_frame_count = 0
            self.error_count = 0
            # Don't open timestamp log yet - wait for first frame

            while not self.stop_flag.is_set():
                time.sleep(self.sleeptime)
                self._process_queue()

            self._close_run()

    
    def _close_run(self):
        # Process any remaining frames in the queue before closing
        self._process_queue()
        self._release_file_handler()
        self._release_ts_log()   # close the single timelog at end of run
        self._clear_queue()  # Clear any remaining items after processing
        self.stop_flag.clear()
        self.is_run_closed.set()

  
    def _process_queue(self):
        while True:
            try:
                self._save_next_in_queue()
            except queue.Empty:
                break
            
    def _save_next_in_queue(self):
        buff = self.inQ.get(timeout = self.queue_timeout)
        self._handle_frame(buff)

    def _handle_frame(self, buff):
        # print(buff, flush=True)
        frame, metadata = buff
        # Handle shared memory tuple from AVT
        if isinstance(frame, tuple) and len(frame) == 3 and isinstance(frame[0], str):
            shm_name, shape, dtype = frame
            try:
                frame, shm = _attach_shm_with_retry(shm_name, shape, dtype)
            except FileNotFoundError:
                self.error_count += 1
                return
            try:
                if (self.file_handler is None or
                    (self.frames_per_file > 0 and np.mod(self.saved_frame_count,
                                                    self.frames_per_file)==0)):
                    self._init_file_handler(frame)
                frameid, timestamp = metadata[:2]
                try:
                    self._write(frame, frameid, timestamp)
                    # Only log timestamp after successful write
                    self._log_timestamp(timestamp)
                    self.saved_frame_count += 1
                except Exception:
                    self.error_count += 1
            finally:
                shm.close()
                shm.unlink()

        else:
            if (self.file_handler is None or
                (self.frames_per_file > 0 and np.mod(self.saved_frame_count,
                                                   self.frames_per_file)==0)):
                self._init_file_handler(frame)
            frameid, timestamp = metadata[:2] 
            try:
                self._write(frame,frameid,timestamp)
                # Only log timestamp after successful write
                self._log_timestamp(timestamp)
                self.saved_frame_count += 1
            except Exception:
                self.error_count += 1
                
    def close(self):
        self.close_flag.set()
        self.stop_flag.set()
        
class TiffWriter(FileWriter):
    def __init__(self,
                 filepath,
                 frames_per_file=256,
                 compression=None):
        
        self.compression = None
        if not compression is None:
            if compression > 9:
                display('Can not use compression over 9 for the TiffWriter')
            elif compression > 0:
                self.compression = compression
                
        super().__init__(filepath,
                         extension = 'tif',
                         frames_per_file=frames_per_file)
        

    def _get_file_handler(self,filepath,frame = None):
        display('Opening: '+ filepath)
        return twriter(filepath)

    def _write(self,frame,frameid,timestamp):
        self.file_handler.save(frame,
                               compress=self.compression,
                               description='id:{0};timestamp:{1}'.format(frameid,timestamp))

class BinaryWriter(FileWriter):
    def __init__(self, filepath,
                       frames_per_file = 0,
                       **kwargs):
        super().__init__(filepath = filepath + "_{n_chan}_{H}_{W}_{dtype}",
                         frames_per_file=frames_per_file,
                         extension = 'dat')
        
    def _get_file_handler(self,filepath,frame = None):
        dtype = frame.dtype
        if dtype == np.float32:
            dtype='float32'
        elif dtype == np.uint8:
            dtype='uint8'
        else:
            dtype='uint16'
        filepath = filepath.format(n_chan = frame.shape[2],
                                        W = frame.shape[1],
                                        H = frame.shape[0],
                                    dtype = dtype)
        display('Opening: '+ filepath)
        return open(filepath,'wb')
        
    def _write(self,frame,frameid,timestamp):
        # Ensure bytes are written
        if isinstance(frame, np.ndarray):
            self.file_handler.write(frame.tobytes(order='C'))
        else:
            self.file_handler.write(frame)
        if np.mod(frameid,5000) == 0: 
            display('Wrote frame id - {0}'.format(frameid))
        
class FFMPEGWriter(FileWriter):
    def __init__(self, filepath,
                       frames_per_file=0,
                       hwaccel = None,
                       frame_rate = None,
                       compression=17,
                       **kwargs):
                        
        super().__init__(filepath,
                         frames_per_file = frames_per_file,
                         extension = 'avi')
                         
        self.compression = compression
        if frame_rate is None:
            frame_rate = 0
        if frame_rate <= 0:
            frame_rate = 30.
        self.frame_rate = frame_rate
        if hwaccel is None:
            self.doutputs = {'-format':'h264',
                             '-pix_fmt':'gray',
                             '-vcodec':'libx264',
                             '-threads':str(10),
                             '-crf':str(self.compression)}
        else:            
            if hwaccel == 'intel':
                if self.compression == 0:
                    display('Using compression 17 for the intel Media SDK encoder')
                    self.compression = 17
                self.doutputs = {'-format':'h264',
                                 '-pix_fmt':'yuv420p',
                                 '-vcodec':'h264_qsv',
                                 '-global_quality':str(25), # specific to the qsv
                                 '-look_ahead':str(1),
                                 # 'preset':'veryfast',  # or 'ultrafast'
                                 '-threads':str(1),
                                 '-crf':str(self.compression)}
            elif hwaccel == 'nvidia':
                if self.compression == 0:
                    display('Using compression 25 for the NVIDIA encoder')
                    self.compression = 25
                self.doutputs = {'-vcodec':'h264_nvenc',
                                 '-pix_fmt':'yuv420p',
                                 '-cq:v':str(self.compression),
                                 '-threads':str(1),
                                 '-preset':'medium'}
        self.hwaccel = hwaccel
    
    def set_video_settings(self,cam):
        ''' Sets camera specific variables - happens after camera load'''
        self.frame_rate = None
        if hasattr(cam,'frame_rate'):
            self.frame_rate = cam.frame_rate
        self.nchannels = 1
        if hasattr(cam,'nchan'):
            self.nchannels = cam.nchan

    def _get_file_handler(self,filepath,frame = None):
        if frame is None:
            raise ValueError('[Recorder] Need to pass frame to open a file.')
        if self.frame_rate is None:
            self.frame_rate = 0
        if self.frame_rate == 0:
            display('Using 30Hz frame rate for ffmpeg')
            self.frame_rate = 30
        
        self.doutputs['-r'] =str(self.frame_rate)
        self.dinputs = {'-r':str(self.frame_rate)}
        
        # does a check for the datatype, if uint16 then save compressed lossless
        if frame.dtype in [np.uint16] and len(frame.shape) == 2:
            filepath = filepath.rsplit(".",1)[0] + '.mov'
            inputdict={'-pix_fmt':'gray16le',
                      '-r':str(self.frame_rate)} # this is important
            outputdict={'-c:v':'libopenjpeg',
                       '-pix_fmt':'gray16le',
                       '-r':str(self.frame_rate)}
        else:
            inputdict=self.dinputs
            outputdict=self.doutputs
        display('Opening: '+ filepath)
        return FFmpegWriter(filepath, inputdict=inputdict, outputdict=outputdict)
            
    def _write(self,frame,frameid,timestamp):
        self.file_handler.writeFrame(frame)

class OpenCVWriter(FileWriter):
    def __init__(self, filepath,
                       frames_per_file = 0,
                       fourcc = 'XVID', #'X264'
                       frame_rate = 60,
                       **kwargs):
        self.frame_rate = frame_rate if frame_rate and frame_rate > 0 else 20
        cv2.setNumThreads(6)
        self.fourcc = cv2.VideoWriter_fourcc(*fourcc)
        self.w = None
        self.h = None
        super().__init__(filepath,
                         extension = 'avi',
                         frames_per_file=frames_per_file)
        
    def _release_file_handler(self):
        if self.file_handler is not None:
            try:
                self.file_handler.release()
            except Exception:
                pass
            finally:
                self.file_handler = None
        # Do NOT touch the timelog here


    def _get_file_handler(self,filepath,frame = None):
        self.w = frame.shape[1]
        self.h = frame.shape[0]
        is_color = frame.ndim == 3 and frame.shape[2] == 3

        if not self.frame_rate or self.frame_rate < 1:
            self.frame_rate = 30
            
        display('Opening: '+ filepath)
        writer = cv2.VideoWriter(filepath, self.fourcc, self.frame_rate,(self.w,self.h), is_color)
        if not writer.isOpened():
            display(f"OpenCV VideoWriter failed to open for {filepath} with fourcc={self.fourcc}. Trying MJPG...", level='warning')
            # Fallback to MJPG
            mjpg = cv2.VideoWriter_fourcc(*'MJPG')
            writer = cv2.VideoWriter(filepath, mjpg, self.frame_rate,(self.w,self.h), is_color)
            if not writer.isOpened():
                display(f"OpenCV VideoWriter still failed to open for {filepath}.", level='error')
        return writer
                                  
    def _write(self,frame,frameid,timestamp):
        img = frame
        # Squeeze singleton channel dimension for grayscale
        if img.ndim == 3 and img.shape[2] == 1:
            img = img[:, :, 0]
        # Convert to 8-bit for OpenCV VideoWriter if needed
        if img.dtype == np.uint16:
            img = (img >> 8).astype(np.uint8)
        elif img.dtype == np.float32 or img.dtype == np.float64:
            img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
        # Ensure proper shape: 2D for gray, 3D (H,W,3) for color
        if img.ndim == 3 and img.shape[2] not in (1, 3):
            # Unknown channel layout; default to grayscale conversion
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.shape[2] > 1 else img
        self.file_handler.write(img)
