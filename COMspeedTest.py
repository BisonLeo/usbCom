#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Very simple serial wrapper with threading

from __future__ import absolute_import

import codecs
import re
from collections import deque
# from pyqtgraph.Qt import QtCore
import sys
import threading
import time as timemodule
import unittest
from enum import Enum


try:
    import Queue
except ImportError:
    import queue as Queue

import traceback

import serial
from serial.tools.list_ports import comports
from serial.tools import hexlify_codec

import logging

logging.basicConfig(level=logging.DEBUG,
                    format='(%(threadName)-9s) %(asctime)s- %(message)s',)

# pylint: disable=wrong-import-order,wrong-import-position

codecs.register(lambda c: hexlify_codec.getregentry() if c == 'hexlify' else None)

TX_Encoder = codecs.getincrementalencoder('hexlify')('replace')
RX_HexDecoder = codecs.getincrementaldecoder('hexlify')('replace')
RX_Decoder = codecs.getincrementaldecoder('UTF-8')('replace')

try:
    raw_input
except NameError:
    # pylint: disable=redefined-builtin,invalid-name
    raw_input = input   # in python3 it's "raw"
    unichr = chr


class Transform(object):
    """do-nothing: forward all data unchanged"""
    def rx(self, text):
        """text received from serial port"""
        return text

    def tx(self, text):
        """text to be sent to serial port"""
        return text

    def echo(self, text):
        """text to be sent but displayed on console"""
        return text


class SingleLR(Transform):
    """Remove multiple line returns"""
    def rx(self, text):
        text = re.subn('\n\n+', '\n', text)[0]
        text = re.subn('\r\n(\r\n)+', '\r\n', text)[0]
        return text

    def tx(self, text):
        return self.rx(text)


class CRLF(Transform):
    """ENTER sends CR+LF"""

    def tx(self, text):
        return text.replace('\n', '\r\n')


class CR(Transform):
    """ENTER sends CR"""

    def rx(self, text):
        return text.replace('\r', '\n')

    def tx(self, text):
        return text.replace('\n', '\r')


class LF(Transform):
    """ENTER sends LF"""

    def rx(self, text):
        return text.replace('\r', '\n')


class Printable(Transform):
    """Show decimal code for all non-ASCII characters and replace most control codes"""

    def rx(self, text):
        r = []
        for c in text:
            if ' ' <= c < '\x7f':  # or c in '\r\n\b\t':
                r.append(c)
            elif c < ' ':
                r.append(unichr(0x2400 + ord(c)))
            else:
                r.extend(unichr(0x2080 + ord(d) - 48) for d in '{:d}'.format(ord(c)))
                r.append(' ')
        return ''.join(r)

    echo = rx


class DebugIO(Transform):
    """Print what is sent and received"""

    def rx(self, text):
        sys.stderr.write(' [RX:{!r}] '.format(text))
        sys.stderr.flush()
        return text

    def tx(self, text):
        sys.stderr.write(' [TX:{!r}] '.format(text))
        sys.stderr.flush()
        return text


# other ideas:
# - add date/time for each newline
# - insert newline after: a) timeout b) packet end character

EOL_TRANSFORMATIONS = {
    'single': SingleLR,
    'crlf': CRLF,
    'cr': CR,
    'lf': LF,
}

TRANSFORMATIONS = {
    'single': SingleLR,
    'direct': Transform,    # no transformation
    'default': Printable,
    'printable': Printable,
    'debug': DebugIO,
}


class PipeDataType(Enum):
    data = 1
    status = 2



class MiniSerial(object):
    """
    Process data from/to serial port
    """
    # exitsignal = QtCore.pyqtSignal()

    def __init__(self, serial_instance, pipeConn=None, echo=False, eol=('crlf',), filters=('single',), exitCallback=None):
        super(MiniSerial, self).__init__()

        self.debugRX = False
        self.echo = False

        self.alive = False
        self._reader_alive = False
        self.receiver_thread = None

        self.writer_thread = None
        self._writer_alive = False

        self.rx_decoder = None
        self.tx_encoder = None
        self.msgQueue = None
        self.exitFiredOnce = 0
        self.changingPort = False

        self.reset()
        self.serial = serial_instance
        self.echo = echo
        self.eol = [eol,] if isinstance(eol, str) else eol
        self.filters = filters
        self.tx_transformations = []
        self.rx_transformations = []
        self.update_transformations()

        if sys.version_info >= (3, 0):
            self.byte_output = sys.stdout.buffer
        else:
            self.byte_output = sys.stdout
        self.output = sys.stdout
        if exitCallback:
            self.exitCallback = exitCallback
            # self.exitsignal.connect(self.exitCallback)
        self.pipeConn = pipeConn

    def reset(self):
        self.serial = None
        self.alive = False
        self._reader_alive = False
        self.receiver_thread = None

        self.writer_thread = None
        self._writer_alive = False

        self.msgQueue = Queue.Queue()
        self.exitFiredOnce = 0
        self.changingPort = False
        self.rx_decoder = RX_Decoder
        self.tx_encoder = TX_Encoder
        logging.debug("reset")

    def write_bytes(self, byte_string):
        """Write bytes (already encoded) to console"""
        self.byte_output.write(byte_string)
        self.byte_output.flush()

    def write(self, text):
        """Write string to console"""
        self.output.write(text)
        self.output.flush()

    def reader(self):
        """loop and copy serial->console"""
        logging.warning("Reader started")
        try:
            while self.alive and self._reader_alive:
                # # check for writting first
                # try:
                #     text = self.msgQueue.get_nowait()
                #     if self.serial and self.serial.is_open:
                #         text = self.tx_encoder.encode(text)
                #         logging.debug("msg after tx_encoder {0}".format(text))
                #         self.serial.write(text)
                #         echo_text = ""
                #     else:
                #         echo_text = "Dummy:"
                #     if self.echo:
                #         echo_text += text
                #         for transformation in self.tx_transformations:
                #             echo_text = transformation.echo(echo_text)
                #         self.write(echo_text)
                # except Queue.Empty as e:
                #     pass

                # read all that is there or wait for one byte
                data = self.serial.read(self.serial.in_waiting or 1)
                if not data:
                    continue
                if self.serial.in_waiting > 0:
                    data += self.serial.read(self.serial.in_waiting)

                if hasattr(self, 'callbck'):
                    self.callbck(data)
                text = self.rx_decoder.decode(data)
                for transformation in self.rx_transformations:
                    text = transformation.rx(text)

                if self.debugRX or self.echo:
                    self.write(text)
        except AttributeError as e:
            logging.error(e.message)
        except serial.SerialException as e:
            mats = re.findall(r"(?P<pre>.*?)(?P<s>'[^']*?')(?P<post>[^']*)", e.message)
            if len(mats) > 0:
                out = "".join([x[0]+eval(x[1])+x[2] for x in mats])
            else:
                out = e.message
            logging.error(codecs.getdecoder('cp936')(out)[0]+'\n')
            self._reader_alive = False
            if hasattr(self.serial, 'cancel_read'):
                self.serial.cancel_read()
            self.alive = False
            if self.writer_thread:
                self.writer_thread.join(1.0)
        except Exception as e:
            logging.error(e.message)
            logging.error(traceback.format_exc())
        finally:
            self.alive = False
            self._reader_alive = False
            logging.warning("Reader Ended")
            if self.serial and self.serial.is_open:
                self.serial.close()
            self.msgQueue.put("dummy for exit")
            if self.exitFiredOnce > 0 and not self.changingPort:
                # self.exitsignal.emit()
                self.reset()
            self.exitFiredOnce += 1

    def _start_reader(self):
        """Start reader thread"""
        self._reader_alive = True
        # start serial->console thread
        self.receiver_thread = threading.Thread(target=self.reader, name='rx')
        self.receiver_thread.daemon = True
        self.receiver_thread.start()

    def _stop_reader(self):
        """Stop reader thread only, wait for clean exit of thread"""
        self._reader_alive = False
        if self.serial and hasattr(self.serial, 'cancel_read'):
            self.serial.cancel_read()
        if self.receiver_thread and self.receiver_thread.isAlive():
            self.receiver_thread.join(1.0)
            self.receiver_thread = None
        else:
            self.exitFiredOnce += 1

    def writer(self):
        logging.warning("writer started")
        try:
            while self.alive and self._writer_alive:
                try:
                    text = self.msgQueue.get()
                    if self.serial and self.serial.is_open:
                        text = self.tx_encoder.encode(text)
                        # logging.debug("msg after tx_encoder {0}".format(text))
                        self.serial.write(text)
                        echo_text = ""
                    else:
                        echo_text = "Dummy:"
                    if self.echo:
                        echo_text += text
                        for transformation in self.tx_transformations:
                            echo_text = transformation.echo(echo_text)
                        self.write(echo_text)
                except Queue.Empty as _:
                    continue
                except AttributeError as e:
                    logging.error(e.message)
        except serial.SerialException as e:
            logging.error(e.message+'\n')
            self._writer_alive = False
            if hasattr(self.serial, 'cancel_write'):
                self.serial.cancel_write()
            self.alive = False
            if self.receiver_thread:
                self.receiver_thread.join(1.0)
        finally:
            self.alive = False
            self._writer_alive = False
            logging.warning("Writer Ended")
            if self.exitFiredOnce > 0 and not self.changingPort:
                # self.exitsignal.emit()
                self.reset()
            self.exitFiredOnce += 1

    def _start_writer(self):
        """Start writer thread"""
        self._writer_alive = True
        # start serial->console thread
        self.writer_thread = threading.Thread(target=self.writer, name='tx')
        self.writer_thread.daemon = True
        self.writer_thread.start()

    def _stop_writer(self):
        """Stop writer thread only, wait for clean exit of thread"""
        self._writer_alive = False
        if self.serial and hasattr(self.serial, 'cancel_write'):
            self.serial.cancel_write()
        if self.writer_thread and self.writer_thread.isAlive():
            self.writer_thread.join(1.0)
            self.writer_thread = None
        else:
            self.exitFiredOnce += 1

    def msg(self, bts):
        logging.debug("Will send {0}".format(bts))
        self.msgQueue.put(bts)

    def start(self):
        """start worker threads"""
        self.alive = True
        if self.serial:
            logging.info('--- Port starting: {} ---'.format(self.serial.port))
            self._start_reader()
            self._start_writer()
            while self.alive and (self.pipeConn is not None or __name__ == "__main__"):
                try:
                    if self.pipeConn:
                        self.pipeConn.recv()
                except EOFError as err:
                    self.alive = False
            self.stop()

    def stop(self):
        """set flag to stop worker threads"""
        self.alive = False
        if self._reader_alive:
            self._stop_reader()
        if self._writer_alive:
            self._stop_writer()

        if self.serial:
            logging.info('--- Port closing: {} ---'.format(self.serial.port))
            self.serial.close()
        else:
            logging.info('stop()')

    def update_transformations(self):
        """take list of transformation classes and instantiate them for rx and tx"""
        transformations = [EOL_TRANSFORMATIONS[f] for f in self.eol] + [TRANSFORMATIONS[f] for f in self.filters]
        self.tx_transformations = [t() for t in transformations]
        self.rx_transformations = list(reversed(self.tx_transformations))

    def changeNewport(self, port):
        new_serial = None

        if port and (port != self.serial.port or not self.serial.is_open):
            try:
                # save settings
                settings = self.serial.getSettingsDict()
                new_serial = serial.serial_for_url(port, do_not_open=True)
                # restore settings and open
                new_serial.applySettingsDict(settings)
                new_serial.rts = self.serial.rts
                new_serial.dtr = self.serial.dtr
                new_serial.open()
                new_serial.break_condition = self.serial.break_condition
            except Exception as e:
                sys.stderr.write('--- ERROR opening new port: {} ---'.format(e))
                if new_serial:
                    new_serial.close()
                return
            else:
                logging.info('--- Port {} closing ---'.format(self.serial.port))
                self.changingPort = True
                self.stop()
                self.serial = new_serial
                self.start()
                logging.info('--- Port changed to: {} ---'.format(self.serial.port))
                self.changingPort = False
        # and restart the reader thread
        if not self.alive:
            self.start()


def openComPortInProcess(portCom, child_conn):
    # TODO: move this to multiprocess
    args, parser = getSysArgs(portCom, 115200)
    filters = args.filter
    try:
        serInstance = serial.serial_for_url(
            args.port,
            args.baudrate, parity=args.parity, rtscts=args.rtscts,
            xonxoff=args.xonxoff,
            do_not_open=True
        )

        if isinstance(serInstance, serial.Serial):
            serInstance.exclusive = args.exclusive

        if portCom != -1:
            mt = MiniSerial(serInstance, pipeConn=child_conn, echo=False,
                                       eol=('single', 'lf'),
                                       filters=filters, exitCallback=None)
            mt.callbck = splitReturn

            if not args.quiet:
                sys.stderr.write(
                    '--- Serial on {p.name}  {p.baudrate},{p.bytesize},{p.parity},{p.stopbits} ---\n'.format(
                        p=mt.serial))

            serInstance.open()
            mt.start()
    except serial.SerialException as e:
        logging.error('could not open port {!r} \n'.format(args.port))
        # self.miniser = None
        # self.closeCom.setEnabled(False)


def portslist():
    return comports()


def getSysArgs(default_port=None, default_baudrate=115200,
               default_rts=None, default_dtr=None):
    import argparse

    parser = argparse.ArgumentParser(
        description='Miniterm - A simple terminal program for the serial port.')

    parser.add_argument(
        'port',
        nargs='?',
        help='serial port name ("-" to show port list)',
        default=default_port)

    parser.add_argument(
        'baudrate',
        nargs='?',
        type=int,
        help='set baud rate, default: %(default)s',
        default=default_baudrate)

    group = parser.add_argument_group('port settings')

    group.add_argument(
        '--parity',
        choices=['N', 'E', 'O', 'S', 'M'],
        type=lambda c: c.upper(),
        help='set parity, one of {N E O S M}, default: N',
        default='N')

    group.add_argument(
        '--rtscts',
        action='store_true',
        help='enable RTS/CTS flow control (default off)',
        default=False)

    group.add_argument(
        '--xonxoff',
        action='store_true',
        help='enable software flow control (default off)',
        default=False)

    group.add_argument(
        '--rts',
        type=int,
        help='set initial RTS line state (possible values: 0, 1)',
        default=default_rts)

    group.add_argument(
        '--dtr',
        type=int,
        help='set initial DTR line state (possible values: 0, 1)',
        default=default_dtr)

    group.add_argument(
        '--non-exclusive',
        dest='exclusive',
        action='store_false',
        help='disable locking for native ports',
        default=True)

    group.add_argument(
        '--ask',
        action='store_true',
        help='ask again for port when open fails',
        default=False)

    group = parser.add_argument_group('data handling')

    group.add_argument(
        '-e', '--echo',
        action='store_true',
        help='enable local echo (default off)',
        default=False)

    group.add_argument(
        '--txencoding',
        dest='tx_encoding',
        metavar='CODEC',
        help='set the encoding for the serial port (e.g. hexlify, Latin1, UTF-8), default: %(default)s',
        default='hexlify')

    group.add_argument(
        '--rxencoding',
        dest='rx_encoding',
        metavar='CODEC',
        help='set the encoding for the serial port (e.g. hexlify, Latin1, UTF-8), default: %(default)s',
        default='UTF-8')

    group.add_argument(
        '-f', '--filter',
        action='append',
        metavar='NAME',
        help='add text transformation',
        default=['single', 'default'])

    group.add_argument(
        '--eol',
        choices=['cr', 'lf', 'crlf'],
        type=lambda c: c.upper(),
        help='end of line mode',
        default='CRLF')

    group = parser.add_argument_group('hotkeys')

    group.add_argument(
        '--exit-char',
        type=int,
        metavar='NUM',
        help='Unicode of special character that is used to exit the application, default: %(default)s',
        default=0x1d)  # GS/CTRL+]

    group.add_argument(
        '--menu-char',
        type=int,
        metavar='NUM',
        help='Unicode code of special character that is used to control miniterm (menu), default: %(default)s',
        default=0x14)  # Menu: CTRL+T

    group = parser.add_argument_group('diagnostics')

    group.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='suppress non-error messages',
        default=False)

    group.add_argument(
        '--develop',
        action='store_true',
        help='show Python traceback on error',
        default=False)

    args = parser.parse_args()
    if args.menu_char == args.exit_char:
        parser.error('--exit-char can not be the same as --menu-char')

    return args, parser


def ask_for_port():
    """\
    Show a list of ports and ask the user for a choice. To make selection
    easier on systems with long device names, also allow the input of an
    index.
    """
    if len(portslist()) < 1:
        return -1
    while True:
        sys.stderr.write('\n--- Available ports:\n')
        ports = []
        for n, (port, desc, hwid) in enumerate(sorted(portslist()), 1):
            sys.stderr.write('--- {:2}: {:20} {!r}\n'.format(n, port, desc))
            ports.append(port)
        port = raw_input('--- Enter port index or full name (any other will relist): ')
        try:
            index = int(port) - 1
            if not 0 <= index < len(ports):
                sys.stderr.write('--- Invalid index!\n')
                continue
        except ValueError:
            pass
        else:
            port = ports[index]
        return port


lineNum = 0
linebuff = deque(['',])
prt = Printable()


# TODO: fix the deliminator in Embed coding to escape space and '\r\n'
def keepEndSpaceShort(text):
    """ short multiple spaces into one space,
    but for starting and ending spaces are kept the same
    """
    i = 0
    j0 = j = len(text)
    while i < j and (text[i] == ' ' or text[j-1] == ' '):
        if text[i] == ' ':
            i += 1
        if text[j-1] == ' ':
            j -= 1
        if i > j:
            j += 1

    text = ' '.join(list(filter(None, text.split(' '))))
    return ' '*i + text + ' '*(j0-j)


lnptn = re.compile(r'(?P<retype>Read|Echo)\s*(?P<reg>(?:\d+))\s*as\s*(?P<value>0x\s*(?:[\-0-9a-fA-F]+))\s*-?\s*(?P<wanted>0x\s*(?:[\-0-9a-fA-F]+))')
statusptn = re.compile(r'ID (?P<status>.{2}): 0x(?P<idstatus>[0-9a-fA-F]{8}):(?P<timestamp>[0-9a-fA-F]{8})')
dataptn = re.compile(r'(?P<timestamp>.{8}):(?P<data>.{16})W', re.DOTALL)


def parseLine(text):
    """Process a line"""
    localptn = statusptn
    mat = localptn.search(text)
    ret = []

    if not mat:
        localptn = lnptn
        mat = localptn.search(text)
        if not mat:
            localptn = dataptn
            mat = localptn.search(text)

    while mat:
        shrt = text[mat.start():mat.end()]
        # shrt = keepEndSpaceShort(shrt)
        ret.append(shrt)
        if localptn == statusptn:
            pass
            # logging.info(mat.groups())
        elif localptn == lnptn:
            try:
                rettype = mat.group('retype')
                reg, value = int(mat.group('reg')), int(mat.group('value').replace(" ", ''), 16)
                if rettype == 'Echo':
                    wanted = mat.group('wanted')
                    if wanted:
                        wanted = int(wanted.replace(" ", ''), 16)
                        logging.debug("Check output {0} as 0x{1:8X}, {2:8X}".format(reg, value, wanted))
                    else:
                        logging.debug("Check output {0} as 0x{1:8X}".format(reg, value))
                else:
                    logging.debug("Read {0:2d} as 0x{1:4X}".format(reg, value))
                    if reg == 0xFF and value == 0xDEADBEAF:
                        logging.debug("unlocking for setting")
                        # ADConsole.BytesLocked = False
                    elif reg == 0xFF and value == 0xBEADDEAF:
                        logging.debug("locking for setting")
                        # ADConsole.BytesLocked = True
                    else:
                        pass
                        # updateParamWith(reg, value)
            except ValueError as e:
                logging.error(e.message)
        elif localptn == dataptn:
            mg = mat.group('data')
            tm = mat.group('timestamp')
            global pipeConn
            if pipeConn:
                pipeConn.send((PipeDataType.data, (tm, mg)))
        else:
            # dataptn
            vw = hexlify_codec.hex_decode(shrt)
            logging.info(vw)
            # logging.info(codecs.getencoder('utf8')(prt.rx(shrt))[0])
        text = text[mat.end():]
        mat = localptn.search(text)
    return ret, text


def splitReturn(line, processor=parseLine, sp='\r\r\r\r\n\n\n\n'):
    """Preprocess asynchronous serial data stream such that two splited parts will be
    joined and processed as single line.

    For example, assuming \\n is used as line split
    the multiple lines are sent as
    1. line one \\nline
    2.  two \\nline three\\n

    param: linebuff, list(deque is used) used to store previous lines,
        last element could be incomplete line without line split
    param: processor, callback function called as processor(linebuff[-1]),
    it should return tuple of (list_of_matched_lines, rest_of_unmatched_text)"""
    global lineNum
    global linebuff
    # if len(line.strip()) == 0:
    #     return

    parts = list(filter(None, line.split(sp)))  # remove '\n'
    # for p in parts:
    #     logging.debug(">>>>" + p)
    # for p in range(len(parts)):
    #     parts[p] = keepEndSpaceShort(parts[p])
    head, restParts = parts[0], parts[1:]
    linebuff[-1] += head
    lines, rest = processor(linebuff[-1])
    if len(lines) > 0:
        linebuff.pop()
        linebuff += lines
        linebuff.append(rest)
    apped = len(lines)
    for p in range(len(restParts)):
        # if linebuff[-1].endswith(restParts[p].strip()):
        #     # ignore multiple empty lines
        #     continue
        lines, rest = processor(restParts[p])
        if len(lines) > 0:
            linebuff.pop()
            linebuff += lines
            linebuff.append(rest)
        else:
            linebuff[-1] += rest
        apped += len(lines)
    while len(linebuff) > 15:
        linebuff.popleft()
    # linebuff = linebuff[-15:]
    lineNum += apped

startTime = None
lastPrintTime = None
totalNum = 0
def calSpeed(data):
    global startTime
    global totalNum, lastPrintTime
    if startTime is None:
        startTime = timemodule.time()
        totalNum = 0

    totalNum += len(data)
    if (lastPrintTime is None) or timemodule.time()> lastPrintTime + 10:
        lastPrintTime = timemodule.time()
        if timemodule.time() > startTime:
            logging.info("data rate is: {sp} Bytes/s..".format(sp=totalNum/(timemodule.time()-startTime)))


# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
# default args can be used to override when calling main() from an other script
# e.g to create a miniterm-my-device.py
def main(default_port=None, default_baudrate=9600, default_rts=None, default_dtr=None):
    """Command line tool, entry point"""

    args, parser = getSysArgs(default_port, default_baudrate,
                              default_rts, default_dtr)

    if args.filter:
        if 'help' in args.filter:
            sys.stderr.write('Available filters:\n')
            sys.stderr.write('\n'.join(
                '{:<10} = {.__doc__}'.format(k, v)
                for k, v in sorted(TRANSFORMATIONS.items())))
            sys.stderr.write('\n')
            sys.exit(1)
        filters = args.filter
    else:
        filters = ['default']

    while True:
        # no port given on command line -> ask user now
        if args.port is None or args.port == '-':
            try:
                args.port = ask_for_port()
            except KeyboardInterrupt:
                sys.stderr.write('\n')
                parser.error('user aborted and port is not given')
            else:
                if not args.port:
                    parser.error('port is not given')
        try:
            serial_instance = serial.serial_for_url(
                args.port,
                args.baudrate,
                parity=args.parity,
                rtscts=args.rtscts,
                xonxoff=args.xonxoff,
                do_not_open=True)

            if not hasattr(serial_instance, 'cancel_read'):
                # enable timeout for alive flag polling if cancel_read is not available
                serial_instance.timeout = 1

            if args.dtr is not None:
                if not args.quiet:
                    sys.stderr.write('--- forcing DTR {}\n'.format('active' if args.dtr else 'inactive'))
                serial_instance.dtr = args.dtr
            if args.rts is not None:
                if not args.quiet:
                    sys.stderr.write('--- forcing RTS {}\n'.format('active' if args.rts else 'inactive'))
                serial_instance.rts = args.rts

            if isinstance(serial_instance, serial.Serial):
                serial_instance.exclusive = args.exclusive

            serial_instance.open()
        except serial.SerialException as e:
            sys.stderr.write('could not open port {!r}: {}\n'.format(args.port, e))
            if args.develop:
                raise
            if not args.ask:
                sys.exit(1)
            else:
                args.port = '-'
        else:
            break

    miniterm = MiniSerial(
        serial_instance,
        echo=args.echo,
        eol=args.eol.lower(),
        filters=filters,
        exitCallback=lambda: logging.error("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!On Exit")
    )
    miniterm.callbck = calSpeed #lambda x: splitReturn(x)

    if not args.quiet:
        logging.info('--- Miniterm on {p.name}  {p.baudrate},{p.bytesize},{p.parity},{p.stopbits} ---\n'.format(
            p=miniterm.serial))

    miniterm.start()
    try:
        while True:
            raw_input()
    except (KeyboardInterrupt, EOFError) as _:
        miniterm.alive = False
    finally:
        miniterm.stop()
    if not args.quiet:
        sys.stderr.write('\n--- exit ---\n')


class TestSplit(unittest.TestCase):
    @staticmethod
    def process(text):
        patn = re.compile(r'a(?P<anything>.*?)Z', re.DOTALL)
        localptn = patn
        mat = localptn.search(text)
        ret = []

        while mat:
            shrt = text[mat.start():mat.end()]
            ret.append(shrt)
            print(repr(shrt))
            text = text[mat.end():]
            mat = localptn.search(text)
        return ret, text

    def testUnicode(self):
        splitReturn(u'hi hi 你好 \nrewq req  \n\n', self.process, sp='\n')
        splitReturn(u'fdas\n\n\naaa', self.process, sp='\n')
        splitReturn(u' contiZnue\n \n\naaa', self.process, sp='\n')
        self.assertEqual(linebuff[-1], 'nue aaa', linebuff)
        splitReturn(u' cont2iZnue\n \n\naaaZZZa1ZZtestate1stZZZa', self.process, sp='\n')
        self.assertEqual(linebuff[-1], 'ZZa')
        ind = len(linebuff)-1
        splitReturn(u' cont3iZnue\n \n\naaaZZZa2ZZtestates2tZZZaZ', self.process, sp='\n')
        self.assertEqual(linebuff[ind], 'a cont3iZ')
        self.assertEqual(linebuff[-1], '')
        ind = len(linebuff) - 1
        splitReturn(u' Zacont4iZnue\n \n\naaaZa3ZZa4ZZtestatestZZZ', self.process, sp='\n')
        # one head is popped out
        self.assertNotEqual(linebuff[ind], 'acont4iZ', linebuff)
        self.assertEqual(linebuff[ind], 'aaaZ', linebuff)
        self.assertEqual(linebuff[-1], 'ZZ')
        self.assertEqual(len(linebuff), 15)


if __name__ == '__main__':
    main()
    # unittest.main()
