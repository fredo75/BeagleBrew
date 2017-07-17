#!/usr/bin/python3
#
# Copyright (c) 2012-2015 Stephen P. Smith
# Copyright (c) 2016-2017 Peter Lawler <relwalretep@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:

# The above copyright notice and this permission notice shall be included
# in all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR
# IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# import random
# import serial
# import sqlite3

from Temp1Wire import Temp1Wire
from Display import NoDisplay
from pidpy import pidpy as PIDController
from Adafruit_BBIO import GPIO

from flask import Flask, render_template, request, jsonify
# from systemd import journal

from datetime import datetime
from logging import getLogger, ERROR
from multiprocessing import Process, Pipe, Queue, current_process
from os import chdir
from queue import Full
from time import time, sleep
from subprocess import Popen, PIPE, call
from xml.etree import ElementTree as ET
# from smbus import SMBus

global parent_connA, parent_connB, parent_connC
global statusQ_A, statusQ_B, statusQ_C
global xml_root, template_name, pinHeatList, pinGPIOList
global brewtime, oneWireDir

app = Flask(__name__, template_folder='templates')

werkzeuglog = getLogger('werkzeug')
werkzeuglog.setLevel(ERROR)


# Parameters that are used in the temperature control process
class param:
    status = {
        "numTempSensors": 0,
        "temp": "0",
        "tempUnits": "C",
        "elapsed": "0",
        "mode": "off",
        "cycle_time": 2.0,
        "duty_cycle": 0.0,
        "boil_duty_cycle": 60,
        "set_point": 0.0,
        "boil_manage_temp": 200,
        "num_pnts_smooth": 5,
        "k_param": 44,
        "i_param": 165,
        "d_param": 4
    }


# main web page
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'GET':
        # render main page
        return render_template(template_name, mode=param.status["mode"],
                               set_point=param.status["set_point"],
                               duty_cycle=param.status["duty_cycle"],
                               cycle_time=param.status["cycle_time"],
                               k_param=param.status["k_param"],
                               i_param=param.status["i_param"],
                               d_param=param.status["d_param"])
    else:
        # request.method == 'POST' (first temp sensor / backwards compatibility)
        # get command from web browser or Android
        # print request.form
        param.status["mode"] = request.form["mode"]
        param.status["set_point"] = float(request.form["setpoint"])
        param.status["duty_cycle"] = float(request.form["dutycycle"])
        # is boil duty cycle if mode == "boil"
        param.status["cycle_time"] = float(request.form["cycletime"])
        param.status["boil_manage_temp"] = float(request.form.get("boilManageTemp", param.status["boil_manage_temp"]))
        param.status["num_pnts_smooth"] = int(request.form.get("numPntsSmooth", param.status["num_pnts_smooth"]))
        param.status["k_param"] = float(request.form["k"])
        param.status["i_param"] = float(request.form["i"])
        param.status["d_param"] = float(request.form["d"])
        # send to main temp control process
        # if did not receive variable key value in POST, the param class default is used
        parent_connA.send(param.status)
        return 'OK'


# post params (selectable temp sensor number)
@app.route('/postparams/<sensorNum>', methods=['POST'])
def postparams(sensorNum=None):
    param.status["mode"] = request.form["mode"]
    param.status["set_point"] = float(request.form["setpoint"])
    param.status["duty_cycle"] = float(request.form["dutycycle"])  # is boil duty cycle if mode == "boil"
    param.status["cycle_time"] = float(request.form["cycletime"])
    param.status["boil_manage_temp"] = float(request.form.get("boilManageTemp", param.status["boil_manage_temp"]))
    param.status["num_pnts_smooth"] = int(request.form.get("numPntsSmooth", param.status["num_pnts_smooth"]))
    param.status["k_param"] = float(request.form["k"])
    param.status["i_param"] = float(request.form["i"])
    param.status["d_param"] = float(request.form["d"])
    # send to main temp control process
    # if did not receive variable key value in POST, the param class default is used
    if sensorNum == "1":
        logstatus("INFO", "got post to temp sensor 1")
        parent_connA.send(param.status)
    elif sensorNum == "2":
        logstatus("INFO", "got post to temp sensor 2")
        if len(pinHeatList) >= 2:
            parent_connB.send(param.status)
        else:
            param.status["mode"] = "No Temp Control"
            param.status["set_point"] = 0.0
            param.status["duty_cycle"] = 0.0
            parent_connB.send(param.status)
            logstatus("INFO", "no heat GPIO pin assigned")
    elif sensorNum == "3":
        logstatus("INFO", "got post to temp sensor 3")
        if len(pinHeatList) >= 3:
            parent_connC.send(param.status)
        else:
            param.status["mode"] = "No Temp Control"
            param.status["set_point"] = 0.0
            param.status["duty_cycle"] = 0.0
            parent_connC.send(param.status)
            logstatus("INFO", "no heat GPIO pin assigned")
    else:
        logstatus("INFO", "Sensor doesn't exist (POST)")
    return 'OK'


# post GPIO
@app.route('/GPIO_Toggle/<GPIO_Num>/<onoff>', methods=['GET'])
def GPIO_Toggle(GPIO_Num=None, onoff=None):
    if len(pinGPIOList) >= int(GPIO_Num):
        out = {"pin": pinGPIOList[int(GPIO_Num)-1], "status": "off"}
        if onoff == "on":
            GPIO.output(pinGPIOList[int(GPIO_Num)-1], ON)
            out["status"] = "on"
            logstatus("INFO", "GPIO Pin %s is toggled on" % pinGPIOList[int(GPIO_Num)-1])
        else:  # off
            GPIO.output(pinGPIOList[int(GPIO_Num)-1], OFF)
            logstatus("INFO", "GPIO Pin %s is toggled off" % pinGPIOList[int(GPIO_Num)-1])
    else:
        out = {"pin": 0, "status": "off"}
    return jsonify(**out)


# get status from BeagleBrew using firefox web browser (first temp sensor / backwards compatibility)
@app.route('/getstatus')  # only GET
def getstatusB():
    # blocking receive - current status
    param.status = statusQ.get()
    return jsonify(**param.status)


# get status from BeagleBrew using firefox web browser (selectable temp sensor)
@app.route('/getstatus/<sensorNum>')  # only GET
def getstatus(sensorNum=None):
    # blocking receive - current status
    if sensorNum == "1":
        param.status = statusQ_A.get()
    elif sensorNum == "2":
        param.status = statusQ_B.get()
    elif sensorNum == "3":
        param.status = statusQ_C.get()
    else:
        logstatus("Sensor doesn't exist (GET)")
        param.status["temp"] = "-999"
    return jsonify(**param.status)


def getbrewtime():
    return (time() - brewtime)


# Stand Alone Get Temperature Process
def gettempProc(conn, myTempSensor):
    p = current_process()
    logstatus("INFO", "Starting: name(%s) pid(%s)" % (p.name, p.pid))
    while (True):
        t = time()
        sleep(.5)  # .1+~.83 = ~1.33 seconds
        num = myTempSensor.readTempC()
        elapsed = "%.2f" % (time() - t)
        conn.send([num, myTempSensor.sensorNum, elapsed])


# Get time heating element is on and off during a set cycle time
def getonofftime(cycle_time, duty_cycle):
    duty = duty_cycle/100.0
    on_time = cycle_time*(duty)
    off_time = cycle_time*(1.0-duty)
    return [on_time, off_time]


# Stand Alone Heat Process using I2C (optional)
# def heatProcI2C(cycle_time, duty_cycle, conn):
#     p = current_process()
#     logstatus("INFO", "Starting: name(%s) pid(%s)" % (p.name, p.pid))
#     bus = SMBus(0)
#     bus.write_byte_data(0x26, 0x00, 0x00)  # set I/0 to write
#     while (True):
#         while (conn.poll()):  # get last
#             cycle_time, duty_cycle = conn.recv()
#         conn.send([cycle_time, duty_cycle])
#         if duty_cycle == 0:
#             bus.write_byte_data(0x26, 0x09, 0x00)
#             sleep(cycle_time)
#         elif duty_cycle == 100:
#             bus.write_byte_data(0x26, 0x09, 0x01)
#             sleep(cycle_time)
#         else:
#             on_time, off_time = getonofftime(cycle_time, duty_cycle)
#             bus.write_byte_data(0x26, 0x09, 0x01)
#             sleep(on_time)
#             bus.write_byte_data(0x26, 0x09, 0x00)
#             sleep(off_time)


# Stand Alone Heat Process using GPIO
def heatProcGPIO(cycle_time, duty_cycle, pinNum, conn):
    p = current_process()
    logstatus("INFO", "Starting: name(%s) pid(%s)" % (p.name, p.pid))
    if pinNum > "0":
        pinString = str(pinNum)
        logstatus("INFO", "%s GPIO.OUT" % pinString)
        GPIO.setup(pinNum, GPIO.OUT)
        while (True):
            while (conn.poll()):  # get last
                cycle_time, duty_cycle = conn.recv()
            conn.send([cycle_time, duty_cycle])
            if duty_cycle == 0:
                logstatus("INFO", "%s OFF" % pinString)
                GPIO.output(pinString, OFF)
                sleep(cycle_time)
            elif duty_cycle == 100:
                logstatus("INFO", "%s ON" % pinString)
                GPIO.output(pinString, ON)
                logstatus("INFO", "Sleeping %s for %s" % (pinString, cycle_time))
                sleep(cycle_time)
            else:
                on_time, off_time = getonofftime(cycle_time, duty_cycle)
                logstatus("INFO", "%s ON" % pinString)
                GPIO.output(pinString, ON)
                sleep(on_time)
                logstatus("INFO", "%s OFF" % pinString)
                GPIO.output(pinNum, OFF)
                logstatus("INFO", "Sleeping %s for %s" % (pinString, off_time))
                sleep(off_time)


def unPackParamInitAndPost(paramStatus):
    # temp = paramStatus["temp"]
    # tempUnits = paramStatus["tempUnits"]
    # elapsed = paramStatus["elapsed"]
    mode = paramStatus["mode"]
    cycle_time = paramStatus["cycle_time"]
    duty_cycle = paramStatus["duty_cycle"]
    boil_duty_cycle = paramStatus["boil_duty_cycle"]
    set_point = paramStatus["set_point"]
    boil_manage_temp = paramStatus["boil_manage_temp"]
    num_pnts_smooth = paramStatus["num_pnts_smooth"]
    k_param = paramStatus["k_param"]
    i_param = paramStatus["i_param"]
    d_param = paramStatus["d_param"]
    logstatus("DEBUG", "Initialising paramaters: mode: %s, cycle_time: %s, duty_cycle: %s, boil_duty_cycle: %s, set_point: %s, boil_manage_temp: %s, num_pnts_smooth: %s, k_param: %s, i_param: %s, d_param: %s"
              % (mode, cycle_time, duty_cycle, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param))
    return mode, cycle_time, duty_cycle, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param


def packParamGet(numTempSensors, myTempSensorNum, temp, tempUnits, elapsed, mode, cycle_time, duty_cycle, boil_duty_cycle, set_point,
                 boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param):
    param.status["numTempSensors"] = numTempSensors
    param.status["myTempSensorNum"] = myTempSensorNum
    param.status["temp"] = temp
    param.status["tempUnits"] = tempUnits
    param.status["elapsed"] = elapsed
    param.status["mode"] = mode
    param.status["cycle_time"] = cycle_time
    param.status["duty_cycle"] = duty_cycle
    param.status["boil_duty_cycle"] = boil_duty_cycle
    param.status["set_point"] = set_point
    param.status["boil_manage_temp"] = boil_manage_temp
    param.status["num_pnts_smooth"] = num_pnts_smooth
    param.status["k_param"] = k_param
    param.status["i_param"] = i_param
    param.status["d_param"] = d_param
    logstatus("DEBUG", "New paramaters: mode: %s, cycle_time: %s, duty_cycle: %s, boil_duty_cycle: %s, set_point: %s, boil_manage_temp: %s, num_pnts_smooth: %s, k_param: %s, i_param: %s, d_param: %s"
              % (mode, cycle_time, duty_cycle, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param))
    return param.status


# Main Temperature Control Process
def tempControlProc(myTempSensor, display, pinNum, readOnly, paramStatus, statusQ, conn):
        logstatus("DEBUG", "tempControlProc: myTempSensor %s, display %s, pinNum %s, readOnly %s, paramStatus %s, statusQ %s, conn %s" % (myTempSensor, display, pinNum, readOnly, paramStatus, statusQ, conn))
        mode, cycle_time, duty_cycle, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param = unPackParamInitAndPost(paramStatus)
        logstatus("DEBUG", "tempControlProc: mode %s, cycle_time %s, duty_cycle %s, boil_duty_cycle %s, set_point %s, boil_manage_temp %s, num_pnts_smooth %s, k_param %s, i_param %s, d_param %s" % (mode, cycle_time, duty_cycle, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_param))
        p = current_process()
        logstatus("INFO", "Starting: name(%s) pid(%s)" % (p.name, p.pid))
        # Pipe to communicate with "Get Temperature Process"
        parent_conn_temp, child_conn_temp = Pipe()
        # Start Get Temperature Process
        ptemp = Process(name="gettempProc", target=gettempProc, args=(child_conn_temp, myTempSensor))
        ptemp.daemon = True
        ptemp.start()

        # Pipe to communicate with "Heat Process"
        parent_conn_heat, child_conn_heat = Pipe()
        # Start Heat Process
        pheat = Process(name="heatProcGPIO", target=heatProcGPIO, args=(cycle_time, duty_cycle, pinNum, child_conn_heat))
        pheat.daemon = True
        pheat.start()

        temp_ma_list = []
        manage_boil_trigger = False

        tempUnits = xml_root.find('Temp_Units').text.strip()
        numTempSensors = 0
        for tempSensorId in xml_root.iter('Temp_Sensor_Id'):
            numTempSensors += 1

        temp_ma = 0.0

        # overwrite log file for new data log
        ff = open(LogDir + LogDataFile + str(myTempSensor.sensorNum) + ".csv", LogFileMode)
        ff.write("elapsed time,temperature,target,heat output\n")
        ff.close()

        readyPIDcalc = False

        while (True):
            readytemp = False
            while parent_conn_temp.poll():  # Poll Get Temperature Process Pipe
                temp_C, tempSensorNum, elapsed = parent_conn_temp.recv()  # non blocking receive from Get Temperature Process

                if temp_C == -99:
                    logstatus("ERROR", "Bad Temp Reading on sensor %s - retry" % (tempSensorNum))
                    continue

                if (tempUnits == 'F'):
                    temp = (9.0/5.0)*temp_C + 32
                else:
                    temp = temp_C

                temp_str = "%3.2f" % temp
                display.showTemperature(temp_str)

                readytemp = True

            if readytemp == True:
                if mode == "auto":
                    temp_ma_list.append(temp)

                    # smooth data
                    temp_ma = 0.0  # moving avg init
                    while (len(temp_ma_list) > num_pnts_smooth):
                        temp_ma_list.pop(0)  # remove oldest elements in list

                    if (len(temp_ma_list) < num_pnts_smooth):
                        for temp_pnt in temp_ma_list:
                            temp_ma += temp_pnt
                        temp_ma /= len(temp_ma_list)
                    else:  # len(temp_ma_list) == num_pnts_smooth
                        for temp_idx in range(num_pnts_smooth):
                            temp_ma += temp_ma_list[temp_idx]
                        temp_ma /= num_pnts_smooth

                    # print "len(temp_ma_list) = %d" % len(temp_ma_list)
                    # print "Num Points smooth = %d" % num_pnts_smooth
                    # print "temp_ma = %.2f" % temp_ma
                    # print temp_ma_list

                    # calculate PID every cycle
                    if (readyPIDcalc is True):
                        duty_cycle = pid.calcPID_reg4(temp_ma, set_point, True)
                        # send to heat process every cycle
                        parent_conn_heat.send([cycle_time, duty_cycle])
                        readyPIDcalc = False
                if mode == "boil":
                    if (temp > boil_manage_temp) and (manage_boil_trigger is True):  # do once
                        manage_boil_trigger = False
                        duty_cycle = boil_duty_cycle
                        parent_conn_heat.send([cycle_time, duty_cycle])

                # put current status in queue
                try:
                    paramStatus = packParamGet(numTempSensors, myTempSensor.sensorNum,
                                               temp_str, tempUnits, elapsed,
                                               mode, cycle_time, duty_cycle,
                                               boil_duty_cycle, set_point,
                                               boil_manage_temp, num_pnts_smooth,
                                               k_param, i_param, d_param)
                    statusQ.put(paramStatus)  # GET request
                except Full:
                    pass

                while (statusQ.qsize() >= 2):
                    statusQ.get()  # remove old status

                logdata(myTempSensor.sensorNum, temp, set_point, duty_cycle)

                readytemp == False

                # if only reading temperature (no temp control)
                if readOnly:
                    continue

            while parent_conn_heat.poll():  # Poll Heat Process Pipe
                cycle_time, duty_cycle = parent_conn_heat.recv()  # non blocking receive from Heat Process
                display.showDutyCycle(duty_cycle)
                readyPIDcalc = True

            readyPOST = False
            while conn.poll():  # POST settings - Received POST from web browser or Android device
                paramStatus = conn.recv()
                mode, cycle_time, duty_cycle_temp, boil_duty_cycle, set_point, boil_manage_temp, num_pnts_smooth, k_param, i_param, d_parami = unPackParamInitAndPost(paramStatus)

                readyPOST = True
            if readyPOST == True:
                if mode == "auto":
                    display.showAutoMode(set_point)
                    logstatus("INFO", "auto selected")
                    pid = PIDController.pidpy(cycle_time, k_param, i_param, d_param)  # init pid
                    duty_cycle = pid.calcPID_reg4(temp_ma, set_point, True)
                    parent_conn_heat.send([cycle_time, duty_cycle])
                if mode == "boil":
                    display.showBoilMode()
                    logstatus("INFO", "boil selected")
                    boil_duty_cycle = duty_cycle_temp
                    duty_cycle = 100  # full power to boil manage temperature
                    manage_boil_trigger = True
                    parent_conn_heat.send([cycle_time, duty_cycle])
                if mode == "manual":
                    display.showManualMode()
                    logstatus("INFO", "manual selected")
                    duty_cycle = duty_cycle_temp
                    parent_conn_heat.send([cycle_time, duty_cycle])
                if mode == "off":
                    display.showOffMode()
                    logstatus("INFO", "off selected")
                    duty_cycle = 0
                    parent_conn_heat.send([cycle_time, duty_cycle])
                readyPOST = False
            sleep(.01)


def logdata(tank, temp, set_point, heat):
    f = open(LogDir + LogDataFile + str(tank) + ".csv", LogFileMode)
    f.write("%s,%3.1f,%3.3f,%3.3f,%3.3f\n" % (datetime.utcnow(), getbrewtime(), temp, set_point, heat))
    f.close()


def logstatus(log_status_level, status_string):
    f = open(LogDir + LogStatusFile + ".log", LogFileMode)
    f.write("%s, %s, %s, %s\n" % (datetime.utcnow(), getbrewtime(), log_status_level, status_string))
    f.close()


if __name__ == '__main__':

    brewtime = time()

    tree = ET.parse('/etc/opt/beaglebrew3_config.xml')
    xml_root = tree.getroot()
    template_name = 'beaglebrew.html'

    root_dir_elem = xml_root.find('RootDir')
    if root_dir_elem is not None:
        chdir(root_dir_elem.text.strip())
    else:
        logstatus("INFO", "No RootDir tag found in config.xml, running from current directory")

    LogDir = xml_root.find('LogDir').text.strip()
    if LogDir == "":
        LogDir = "/var/log/beaglebrew3/"

    LogDataFile = xml_root.find('LogDataFile').text.strip()
    if LogDataFile == "":
        LogDataFile = "BeagleBrewData"

    LogStatusFile = xml_root.find('LogStatusFile').text.strip()
    if LogStatusFile == "":
        LogStatusFile = "BeagleBrewStatus"

    LogFileMode = xml_root.find('LogFileMode').text.strip()
    if LogFileMode == "Overwrite":
        # See https://docs.python.org/2/library/functions.html#open
        LogFileMode = "w"
    else:
        LogFileMode = "a"

    SQLite3Dir = xml_root.find('SQLite3Dir').text.strip()
    if SQLite3Dir == "":
        SQLite3Dir = "/var/lib/beaglebrew3"

    SQLite3File = xml_root.find('SQLite3File').text.strip()
    if SQLite3File == "":
        SQLite3File = "beaglebrew.db"

    display = NoDisplay()

    gpioInverted = xml_root.find('GPIO_Inverted').text.strip()
    if gpioInverted == "0":
        ON = 1
        OFF = 0
    else:
        ON = 0
        OFF = 1
    logstatus("INFO", "GPIO Inversion set: On = %s Off = %s" % (ON, OFF))
    vesselList = []
    for vessel in xml_root.iter('Vessel'):
        vesselList.append(vessel.text.strip())
    pinHeatList = []
    for pin in xml_root.iter('Heat_Pin'):
        logstatus("INFO", "Setting up GPIO Pin %s for heat output" % pin)
        pinHeatList.append(pin.text.strip())
    pinGPIOList = []
    for pin in xml_root.iter('GPIO_Pin'):
        pinGPIOList.append(pin.text.strip())
    for pinNum in pinGPIOList:
        logstatus("INFO", "Setting up GPIO Pin %s for manual output" % pinNum)
        GPIO.setup(str(pinNum), GPIO.OUT)
    for tempSensorId in xml_root.iter('Temp_Sensor_Id'):
        myTempSensor = Temp1Wire(tempSensorId.text.strip())
        if len(pinHeatList) >= myTempSensor.sensorNum + 1:
            pinNum = pinHeatList[myTempSensor.sensorNum]
            readOnly = False
        else:
            pinNum = 0
            readOnly = True
        if myTempSensor.sensorNum >= 1:
            display = Display.NoDisplay()
        if myTempSensor.sensorNum == 0:
            statusQ_A = Queue(2)  # blocking queue
            parent_connA, child_conn = Pipe()
            p = Process(name="tempControlProc", target=tempControlProc,
                        args=(myTempSensor, display, pinNum, readOnly,
                              param.status, statusQ_A, child_conn))
            p.start()
        if myTempSensor.sensorNum == 1:
            statusQ_B = Queue(2)  # blocking queue
            parent_connB, child_conn = Pipe()
            p = Process(name="tempControlProc", target=tempControlProc,
                        args=(myTempSensor, display, pinNum, readOnly,
                              param.status, statusQ_B, child_conn))
            p.start()
        if myTempSensor.sensorNum == 2:
            statusQ_C = Queue(2)  # blocking queue
            parent_connC, child_conn = Pipe()
            p = Process(name="tempControlProc", target=tempControlProc,
                        args=(myTempSensor, display, pinNum, readOnly,
                              param.status, statusQ_C, child_conn))
            p.start()

    app.debug = True
    app.run(use_reloader=False, host='0.0.0.0', threaded=True)
