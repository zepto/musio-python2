#!/usr/bin/env python
# vim: sw=4:ts=4:sts=4:fdm=indent:fdl=0:
# -*- coding: UTF8 -*-
#
# Player functions
# Copyright (C) 2012 Josiah Gordon <josiahg@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


""" Player functions

"""

from __future__ import print_function
from multiprocessing import Process, Manager, Pipe
from io import SEEK_SET, SEEK_CUR, SEEK_END
from functools import wraps as functools_wraps
from platform import python_implementation

py_imp = python_implementation()

from sys import stdout as sys_stdout

from .io_util import open_file, open_device


def _play_proc(msg_dict):
    """ Player process

    """

    # from oss_io import Oss as AudioIO
    # from alsa_io import Alsa as AudioIO
    from portaudio_io import Portaudio as AudioIO

    from all_file import AllFile as Music

    with Music(**msg_dict) as music, AudioIO(rate=music.rate,
                                             channels=music.channels,
                                             depth=music.depth,
                                             bigendian=music.bigendian,
                                             unsigned=music.unsigned) as \
            audio_out:

        music.loops = msg_dict.get('loops', -1)
        for buf in music:
            written = audio_out.write(buf)
            if not msg_dict['playing'] or not buf and not written:
                break


def play(filename, **kwargs):
    """ play(filename, soundfont=None, loops=-1) -> Starts playing filename and
    returns an object to send to stop to stop it playing.

    """

    playing = Manager().dict()
    playing['playing'] = True
    playing['filename'] = filename
    playing.update(kwargs)
    play_t = Process(target=_play_proc, args=(playing,))

    play_t.start()

    return playing, play_t


def stop(player_tup):
    """ stop(player_tup) -> Stop the player.

    """

    playing, play_t = player_tup
    playing['playing'] = False
    play_t.join()


class AudioPlayer(object):
    """ Play audio files.

    """

    def __init__(self, filename='', show_position=False, **kwargs):
        """ AudioPlayer(filename='', show_position=False, **kwargs) -> Open
        filename and an appropriate audio io for it.

        """

        self._filename = filename
        self._show_position = show_position

        # Setup the msg_dict for sending messages to the child process.
        self._msg_dict = Manager().dict()

        # Create a pipe for sending and receiving messages.
        self._control_conn, self._player_conn = Pipe()

        # Open the file.
        if filename:
            self.open(filename, **kwargs)

    def __str__(self):
        """ The information about the open file.

        """

        # Wait for the stream to open.
        while 'info' not in self._msg_dict: pass

        # Return the info string.
        return self._msg_dict.get('info', '')

    def __repr__(self):
        """ __repr__ -> Returns a python expression to recreate this instance.

        """

        repr_str = "filename='%(_filename)s'" % self.__dict__

        return '%s(%s)' % (self.__class__.__name__, repr_str)

    def __enter__(self):
        """ Provides the ability to use pythons with statement.

        """

        try:
            return self
        except Exception as err:
            print(err)
            return None

    def __exit__(self, exc_type, exc_value, traceback):
        """ Stop playback when finished.

        """

        try:
            self.stop()
            self._control_conn.close()
            self._player_conn.close()
            return not bool(exc_type)
        except Exception as err:
            print(err)
            return False

    def __del__(self):
        """ Stop playback before deleting.

        """

        self.stop()

    def __len__(self):
        """ The length of the file if it has one.

        """

        return self.length if self.length >= 0 else 0

    def playing_wrapper(func):
        """ Wrap methods and only call them if the stream is playing

        """

        @functools_wraps(func)
        def wrapper(self, *args, **kwargs):
            """ Check if stream is playing and if it is then call func
            otherwise print a message and exit.

            """

            if not self.playing:
                print("%(filename)s is not playing." % self._msg_dict)
                return None

            return func(self, *args, **kwargs)

        return wrapper

    def _play_proc(self, msg_dict, pipe):
        """ Player process

        """

        # Open the file to play.
        with open_file(cached=True, **msg_dict) as fileobj:

            # Put the file info in msg_dict.
            msg_dict['info'] = str(fileobj)
            msg_dict['length'] = fileobj.length

            # Open an audio output device that can handle the data from
            # fileobj.
            with open_device(fileobj, 'w', cached=True, **msg_dict) as device:

                # Set the default number of loops to infinite.
                fileobj.loops = msg_dict.get('loops', -1)

                # Initialize variable.
                buf = b'\x00' * device.buffer_size
                written = 0

                # Loop until stopped or nothing read or written.
                while msg_dict['playing'] and (buf or msg_dict['paused']):
                    # Print the stream position.
                    if msg_dict.get('show_position', False):
                        # Only print the position if the stream has a
                        # length.
                        if fileobj.length > 0:
                            # Calculate the percentage played.
                            pos = (fileobj.position * 100) / float(fileobj.length)

                            # Make the string.
                            pos_str = 'Position: %.2f%%' % pos

                            # Find the length of the string.
                            format_len = len(pos_str) + 2

                            # Print the string and after erasing the old
                            # one using ansi escapes.
                            if py_imp == 'PyPy':
                                # Running in pypy which doesn't have the
                                # flush parameter in the print function.
                                print('\033[%dD\033[K%s' % (format_len,
                                    pos_str), end='')
                                sys_stdout.flush()
                            else:
                                print('\033[%dD\033[K%s' % (format_len,
                                    pos_str), end='')
                                sys_stdout.flush()

                    # Keep playing if not paused.
                    if not msg_dict.get('paused', False):
                        # Read the next buffer full of data.
                        buf = fileobj.readline()

                        # Filler for end of partial buffer to elminiate
                        # end of audio noise.
                        buf_filler = b'\x00' * (device.buffer_size - len(buf))

                        # Write buf.
                        written = device.write(buf + buf_filler)
                    else:
                        # Write a buffer of null bytes so the audio
                        # system can keep its buffer full.
                        device.write(b'\x00' * device.buffer_size)

                    # Get and process any commands from the parent process.
                    if pipe.poll():
                        # Get the data into temp.
                        command = pipe.recv()

                        if 'getposition' in command:
                            pipe.send(fileobj.position)
                        elif 'setposition' in command:
                            fileobj.position = command['setposition']
                        elif 'getloops' in command:
                            pipe.send(fileobj.loops)
                        elif 'setloops' in command:
                            fileobj.loops = command['setloops']
                        elif 'getloopcount' in command:
                            pipe.send(fileobj.loop_count)

        # Set playing to False for the parent.
        msg_dict['playing'] = False

    def open(self, filename, **kwargs):
        """ open(filename) -> Open an audio file to play.

        """

        # Stop the current file from playing.
        self.stop()

        # Set the new filename.
        self._filename = filename

        # Reset the message dictionary so none of the old info is
        # re-used.
        self._msg_dict.clear()

        # Fill the message dictionary with the new info.
        self._msg_dict['show_position'] = self._show_position
        self._msg_dict['filename'] = filename
        self._msg_dict.update(kwargs)

        # Pause it so when we call play later it will start the player
        # but not the audio playback.  Call play again to start audio
        # playback.
        self.pause()

        # Start the playback process in a paused state.  Requires a
        # second call to play to un-pause.
        self.play()

    def play(self):
        """ play() -> Start playback.

        """

        if not self._msg_dict.get('playing', False):
            # Set playing to True for the child process.
            self._msg_dict['playing'] = True

            # Open a new process to play a file in the background.
            self._play_p = Process(target=self._play_proc,
                                   args=(self._msg_dict, self._player_conn))

            # Start the process.
            self._play_p.start()
        elif self._msg_dict.get('paused', True):
            # Un-pause if paused.
            self._msg_dict['paused'] = False

    def stop(self):
        """ stop() -> Stop playback.

        """

        if self._msg_dict.get('playing', False):
            # Stop playback.
            self._msg_dict['playing'] = False

            # Wait for the player process to stop.
            self._play_p.join()

            # Un-Pause.
            self._msg_dict['paused'] = False

    def pause(self):
        """ pause() -> Pause playback.

        """

        # Pause playback.
        self._msg_dict['paused'] = True

    @property
    def paused(self):
        """ True if playback is paused.

        """

        return self._msg_dict.get('paused', False)

    @property
    def playing(self):
        """ True if playing.

        """

        return self._msg_dict.get('playing', False)

    @property
    def length(self):
        """ Length of audio.

        """

        return self._msg_dict.get('length', 0)

    @property
    @playing_wrapper
    def position(self):
        """ Current position.

        """

        self._control_conn.send('getposition')
        return self._control_conn.recv()

    @position.setter
    @playing_wrapper
    def position(self, value):
        """ Set the current position.

        """

        self._control_conn.send({'setposition': int(value)})

    @property
    @playing_wrapper
    def loops(self):
        """ Number of times to loop (playback time + 1).

        """

        self._control_conn.send('getloops')
        return self._control_conn.recv()

    @loops.setter
    @playing_wrapper
    def loops(self, value):
        """ Number of times to loop (playback time + 1).

        """

        self._control_conn.send({'setloops': int(value)})

    @property
    @playing_wrapper
    def loop_count(self):
        """ Number of times the player has looped.

        """

        self._control_conn.send('getloopcount')
        return self._control_conn.recv()

    @playing_wrapper
    def seek(self, offset, whence=SEEK_SET):
        """ seek(position) -> Seek to position in mod.

        """

        if whence == SEEK_CUR:
            self.position += offset
        elif whence == SEEK_END:
            self.position = self.length - offset
        else:
            self.position = offset

        return self.position

    @playing_wrapper
    def tell(self):
        """ tell -> Returns the current position.

        """

        return self.position
