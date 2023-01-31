#!/usr/bin/env python
'''Shopbot GUI functions for controlling fluigent mass flow controller separate threads'''

# external packages
from PyQt5.QtCore import pyqtSignal, QMutex, QObject, QRunnable, Qt, QThread, QTimer, QThreadPool
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import QLabel, QColorDialog, QCheckBox, QFormLayout, QGridLayout, QLineEdit, QMainWindow, QVBoxLayout, QWidget
import pyqtgraph as pg
import csv
import time
import datetime
import numpy as np
from typing import List, Dict, Tuple, Union, Any, TextIO
import logging
import os, sys
import traceback

# local packages
import Fluigent.SDK as fgt
from config import cfg
from general import *

   
#----------------------------------------------------------------------

def pressureConversion():
    return {'mbar':1, 'kpa':0.1, 'psi':0.0145038}

def convertPressure(val:float, oldUnits:str, newUnits:str) -> float:
    '''convert the pressure from old units to new units'''
    if oldUnits==newUnits:
        return val
    if type(val) is str:
        try:
            val = float(val)
        except ValueError:
            return val
    convert = pressureConversion()
    factor = convert[newUnits.lower()]/convert[oldUnits.lower()]
    out = val*factor
    if newUnits.lower()=='mbar':
        out = int(out)
    elif newUnits.lower()=='kpa':
        out = round(out,1)
    elif newUnits.lower()=='psi':
        out = round(out,2)
    
    return out


class plotWatch(QMutex):
    '''Holds the pressure/time list for all channels'''

    def __init__(self, pChans:int, uvChans:int, trange:float, dt:float, units:str, pmax:float):
        super().__init__()
        self.stop = False          # tells us to stop reading pressures
        self.pChans = pChans   # number of channels
        self.uvChans = uvChans
        self.numChans = pChans+uvChans
        self.trange = cfg.fluigent.trange       # time range
        self.dt = dt
        self.d0 = datetime.datetime.now()
        self.units = units
        self.pmax = pmax
        self.initializePList()

        
    def initializePList(self) -> None:
        '''initialize the pressure list and time list'''
        # initialize the time range
        self.time = list(np.arange(-self.trange*1, 0, self.dt/1000)) 
               
        # initialize pressures. assume 0 before we initialized the gui    
        self.pressures = []
        
        for i in range(self.numChans):
            press = [0 for _ in range(len(self.time))]
            self.pressures.append(press)
            
    def updateUnits(self, newUnits:str) -> None:
        '''convert to units'''
        oldUnits = self.units
        self.units = newUnits
        convert = pressureConversion()
        factor = convert[newUnits.lower()]/convert[oldUnits.lower()]
        for i,p in enumerate(self.pressures):
            if i<self.pChans:
                self.pressures[i] = [x*factor for x in p]
                # don't convert uv values


class fluSignals(QObject):
    '''Signals connector that lets us send status updates back to the GUI from the fluPlot object'''
    
    error = pyqtSignal(str, bool)
    progress = pyqtSignal()
    
    
def checkPressure(channel:int) -> int:
    '''reads the pressure in mbar of a given channel, 0-indexed'''
    pressure = int(fgt.fgt_get_pressure(channel))
    return pressure

def setPressure(channel:int, runPressure:float, units:str) -> None:
    '''convert to mbar and set the pressure'''
    p_mbar = int(convertPressure(runPressure, units, 'mbar')) # convert to mbar
    fgt.fgt_set_pressure(channel, p_mbar)
            
        
class plotUpdate(QObject):
    '''plotUpdate updates the list of times and pressures and allows us to read pressures continuously in a background thread.'''
    
    def __init__(self, pw:plotWatch, arduino, connected:bool):
        super().__init__()   
        self.pw = pw                  # plotWatch object (stores pressure list)
        self.numChans = pw.numChans   # number of channels
        self.pChans = pw.pChans 
        self.signals = fluSignals()   # lets us send messages and data back to the GUI
        self.connected = connected  # if the fluigent is connected
        self.pw.lock()
        self.dt = self.pw.dt   # dt in milliseconds
        self.pw.unlock()
        self.arduino = arduino
        
    def checkPressure(self, channel:int, units:str) -> int:
        if not self.connected:
            return 0
        if channel<self.pChans:
            # pressure channel
            pnew_mbar = checkPressure(channel)
            pnew = convertPressure(pnew_mbar, 'mbar', units)
            return pnew
        else:
            # uv lamp
            if self.arduino.uvOn:
                return self.pw.pmax
            else:
                return 0
            

    @pyqtSlot()
    def run(self) -> None:
        '''update the plot and displayed pressure'''
        while True:
            try:
                # get initial list from plotwatch
                self.pw.lock()
                newtime = self.pw.time
                newpressures = self.pw.pressures  
                d0 = self.pw.d0   # initial time
                stop = self.pw.stop
                units = self.pw.units
                self.dt = self.pw.dt   # dt in milliseconds
                self.pw.unlock()
                
                if stop:
                    return
                
                newtime = newtime[1:]                   # Remove the first y element.
                dnow = datetime.datetime.now()          # Finds current time relative to when the plot was created
                tnow = (dnow-d0).total_seconds()
                newtime.append(tnow)         # Add the current time to the list
                for i in range(self.numChans):
                    newpressures[i] = newpressures[i][1:]
                    pnew = self.checkPressure(i, units)
                    newpressures[i].append(pnew)         # Add the current pressure to the list, for each channel
            except Exception as e:
                self.signals.error.emit(f'Error reading pressure: {e}', True)
            else:
                # update plotwatch
                self.pw.lock()
                self.pw.time = newtime                   # Save lists to plotWatch object
                self.pw.pressures = newpressures   
                self.pw.unlock()
                self.signals.progress.emit()             # Tell the GUI to update plot
            
            time.sleep(self.dt/1000)
            