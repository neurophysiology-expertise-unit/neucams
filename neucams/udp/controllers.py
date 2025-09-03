"""controllers.py
Contains the classes to interface with other computers softwares over the network.
The messages are typically simple: send folder name / start acquisition / stop acquisition.
"""
import sys
import os
from os.path import join as pjoin
import socket 
import time
from _controller.utils import display
from PyQt5.QtTest import QTest
try:
    import zmq # to control labcams
except:
    print('ZMQ not installed?')
qWait = QTest.qWait

# TODO common Controller parent class

class ScanboxController(socket.socket):
    '''
    "scanbox": {
        "ip":"10.86.1.94",
        "port":7000,
        "dataFolder": "J:\\data\\2p\\raw\\", 
        "srate": 30.01, 
        "trigger":true,
        "cmd": {
                "startcommand":"g",
                "durationcommand":"d",
                "stopcommand":"S"
                }
    },
    '''
    def __init__(self,ip=None, port = None,
                 cmd = dict(startcommand = 'g',
                              durationcommand = 'd',
                              stopcommand = 'S')):
        super(ScanboxController,self).__init__(socket.AF_INET,
                                               socket.SOCK_DGRAM)
        # set up udp comm
        self.IP = ip       #"10.86.1.130"
        self.PORT = port   #7000 
        self.nFrames =   None
        self.cmd = cmd
        
    def sendMsg(self,msg):
        if not self.IP is None:
            if sys.version_info[0]==3:
                msg = msg+'\n'
                msg = msg.encode(encoding='utf-8', errors='strict')
            else:
                msg = msg+'\n'

            self.sendto(msg, (self.IP, self.PORT))
            print("Sending udp message to SBX: %s" % msg)
            qWait(200)
          
    def setExperiment(self,dataFolder,session,duration):
        self.sendMsg('E000') # reset experiment counter in SB GUI
        self.sendMsg('D%s' % dataFolder) # set dir name       
        self.sendMsg('A%s' % session) # set experiment name
        #duration += 2.*5
        self.sendMsg(self.cmd['durationcommand'] + '%d' % int(duration)) # DURATION DO IT!
        display('Setting scanbox experiment [{0}] Duration = {1}'.format(self.IP,duration))
    def stopAcquisition(self):
        if not self.cmd['stopcommand'] == '':
            #self.sendMsg(self.cmd['stopcommand'])
            print('The "S" command is disabled for now... (BV)')
    def startAcquisition(self):
        self.sendMsg(self.cmd['startcommand'])

class SpikeGLxController(object):
    def __init__(self,ip=None, port = None,
                 root_folder = None,
                 root_folder_map = None,
                 cmd = dict(startcommand = 'SETRECORDENAB 1',
                              stopcommand = 'SETRECORDENAB 0')):
        super(SpikeGLxController,self).__init__()
        # set up udp comm
        self.IP = ip       #"10.86.1.130"
        self.PORT = port   #7000 
        self.nFrames = None
        self.cmd = cmd
        self.waittime = 100
        self.root_folder = root_folder
        self.root_folder_map = root_folder_map
    def sendMsg(self,msg,BUFFER_SIZE = 2):
        print("Sending udp message to spikeglx: %s" % msg,flush=True)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.IP, self.PORT))
        msg += "\n"
        msg = msg.encode(encoding='utf-8', errors='strict')
        print(msg,flush=True)
        s.send(msg)
        data = s.recv(BUFFER_SIZE)
        print(data)
        s.close()
        qWait(self.waittime)
          
    def setExperiment(self,dataFolder,session):
        if not self.root_folder_map is None:
            folder = pjoin(self.root_folder_map,dataFolder)
            if not os.path.isdir(folder):
                print('Creating folder {0}.'.format(pjoin(self.root_folder,dataFolder)))
                os.makedirs(folder)
        self.sendMsg('SETDATADIR {0}'.format(pjoin(self.root_folder,dataFolder)))
        self.sendMsg('SETRUNNAME {0}'.format(session)) 
        display('Setting spikeGLX folder name [{0}]'.format(self.IP))
    def stopAcquisition(self):
        if not self.cmd['stopcommand'] == '':
            self.sendMsg(self.cmd['stopcommand'])
    def startAcquisition(self):
        self.sendMsg(self.cmd['startcommand'])


class LabcamsController(object):
    def __init__(self,ip=None, port = None):
        super(LabcamsController,self).__init__()
        # set up udp comm
        self.IP = ip       #"10.86.1.130"
        self.PORT = port   #7000
        if sys.version_info[0]==3:
            self.zmqprotocol = 2
        elif sys.version_info[0]==2:
            self.zmqprotocol = 3 # highest by default

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect('tcp://{0}:{1}'.format(self.IP,self.PORT))
    def sendMsg(self,msg):
      self.socket.send_pyobj(msg,flags=zmq.NOBLOCK,protocol=self.zmqprotocol),
      msg = self.socket.recv_pyobj()
      print(msg)
    def setExperiment(self,dataFolder,session):
        self.sendMsg({'action':'expName','value':dataFolder + '\\' + session})
        display('Set labcams experiment')
    def setSave(self): # TODO add option to stop the camera by software
        self.sendMsg({'action':'trigger'})
        display('Triggered save on the cameras')


class DMDController(object):
    def __init__(self, patterndir, ip='0.0.0.0', port=2222):
        self.IP = ip
        self.PORT = port
        self.patterndir = patterndir
        self.pattern_list = None
        self.img_list = None
        self.socket = None

    @staticmethod
    def convert8bit(img8):
        """ Convert 8-bit grayscale image to 1-bit black-white image """
        # check if image is 8-bit
        if np.any(img8 > 255):
            raise ValueError('The pattern images should be 8-bit!')

        s = img8.shape
        a = img8.reshape(s[0]*s[1]//8, 8)
        a2 = np.ones(a.shape)
        for i in range(7):
            a2[:, i] = 1 << (7 - i)
        a = a*a2
        return a.sum(axis=1)
    
    def make_pattern_list(self):
        """ Reads the images from the pattern dir, converts them to 1-bit images"""
        pattern_list = []
        img_list = []

        file_list = os.listdir(self.patterndir)

        for file in file_list:
            img = cv2.imread(pjoin(self.patterndir,file), cv2.IMREAD_GRAYSCALE)
            img = ((img > 127) * np.ones(img.shape)).astype(np.uint8)
            img_list.append(img)

            width = img.shape[1]
            n = width % 8
            if n != 0:
                pad = np.zeros((img.shape[0], 8 - n), dtype=np.uint8)
                img = np.hstack((img, pad))
            pattern_list.append(self.convert8bit(img).astype(np.uint8))
        
        self.img_list = img_list
        self.pattern_list = pattern_list
        return pattern_list

    def set_func(self,func_mode):
        """ Set the functionality of the DMD TCP/IP connection 
        
            Below, the variable can be changed to either "1" or "2", which affects how the image is interpreted by PolyScan2.
            If "1", the uploaded image will fit within the EWA of the Polygon
            (e.g. entire image will be seen in EWA)
            If "2", the uploaded image will be truncated, so only the portion of the image within the EWA will be projected.
            (e.g. image fits within camera window, but only the portion of the image seen in EWA will be projected)
        """

        if func_mode > 2:
            raise ValueError('func_mode can only be 1 or 2, got {0}'.format(func_mode))

        if self.socket is None:
            self.connect()

        try:
            func = np.uint32(func_mode)
            self.socket.send(func)
            display('DMD mode set to fit the{0}'.format('WORKING AREA' if func_mode==1 else 'CAMERA WINDOW'))
            return 1
        except:
            display('Unable to set DMD mode')
            self.disconnect()
            return -1

    def set_pattern(self,pattern_id,func_mode=1):
        """ Sends the shape info and the pattern"""
        if pattern_id > len(self.pattern_list):
            raise ValueError('Invalid pattern id!')

        if self.socket is None:
            self.connect()

        is_func_set = self.set_func(func_mode)

        if is_func_set:
            pattern = self.pattern_list[pattern_id]
            img = self.img_list[pattern_id]

            w = np.uint32(img.shape[1]) 
            h = np.uint32(img.shape[0])
            try:
                self.socket.send(w)
                self.socket.send(h)
                self.socket.send(pattern)
                display('Pattern set!')
                return 1
            except:
                display('Failed to set pattern')
                self.disconnect()
                return -1
                  
    def set_multi_pattern(self,func_mode=1,timeout=0.3):
        """ Sets a series of patterns in sequence """
        for p in range(len(self.pattern_list)):
            self.set_pattern(p,func_mode=func_mode)
            time.sleep(timeout)

    def connect(self):
        if self.socket is None:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.IP, self.PORT))
            display('Connected to DMD Server at {0}:{1}'.format(self.IP,self.PORT))

    def disconnect(self):
        if not self.socket is None:
            self.socket.close()
            self.socket = None
            display('Closed connection to DMD Server')



