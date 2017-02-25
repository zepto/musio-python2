#!/usr/bin/env python
# vim: sw=4:ts=4:sts=4:fdm=indent:fdl=0:
# -*- coding: UTF8 -*-
#
# A module to handle the reading of media files using ffmpeg.
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


""" A module for reading media files using ffmpeg.

"""

from .io_base import AudioIO, io_wrapper
from .io_util import msg_out
# from .ffmpeg import _av

from .import_util import LazyImport

_av = LazyImport('ffmpeg.av', globals(), locals(), ['av'], 1)

__supported_dict = {
    'ext': ['.webm', '.flv', '.iflv', '.wma', '.wmv', '.avi', '.mpg', '.m4a'],
    'protocol': ['http'],
    'handler': 'FFmpegFile',
    'dependencies': {
        'ctypes': ['avcodec', 'avdevice', 'avformat', 'postproc', 'swscale'],
        'python': []
    }
}


class FFmpegFile(AudioIO):
    """ A file like object for reading media files with ffmpeg.

    """

    # Only reading is supported
    _supported_modes = 'r'

    def __init__(self, filename, depth=16, rate=44100, channels=2, **kwargs):
        """ FFmpegFile(filename, depth=16, rate=44100, channels=2) ->
        Initialize the playback settings of the player.

        """

        super(FFmpegFile, self).__init__(filename, 'r', depth, rate, channels)

        self.__network_stream = False

        self.__codec_context = None
        self.__audio_stream = None

        self.__format_context = self._open(filename)

        # The length of the file is in av time base units.  To get the actual
        # time devide it by 1000, but we use this to seek.
        self._length = self.__format_context.contents.duration

        self._data = b''
        self._seek_pos = -1

    def _check(self, err):
        """ Check if there was an error and print the result.

        """

        if err < 0:
            errbuf = _av.create_string_buffer(128)
            _av.av_strerror(err, errbuf, _av.sizeof(errbuf))
            msg_out(err, errbuf.raw.decode('utf8', 'replace'))

        return err

    def _set_position(self, position):
        """ Change the position of playback.

        """

        # We have to seek when the stream is ready not now.
        self._seek_pos = position

    def _get_position(self):
        """ Updates the position variable.

        """

        # return _av.avio_seek(self.__format_context.contents.pb, 0, _av.SEEK_CUR)

        # Update the position.
        stream = self.__format_context.contents.streams[self.__audio_stream]
        # We have to multiply the current position by the time base units so it
        # will correspond to the duration, and allow us to seek.
        return stream.contents.cur_dts * stream.contents.time_base.den

    def _open(self, filename):
        """ _open(filename) -> Load the specified file.

        """

        filename = filename.encode('utf-8', 'surrogateescape')

        # Initialize ffmpeg.
        _av.avcodec_register_all()
        _av.av_register_all()
        _av.avdevice_register_all()

        # Check if it is a network stream.
        if b'://' in filename:
            _av.avformat_network_init()
            self.__network_stream = True

        # Create a format context, open the file and find the stream info.
        format_context = _av.avformat_alloc_context()
        self._check(_av.avformat_open_input(format_context, filename, None,
                                            None))
        self._check(_av.avformat_find_stream_info(format_context, None))

        # Deprecated.
        # self._check(_av.av_open_input_file(format_context, filename, None, 0,
        #                                    None))
        # Deprecated.
        # self._check(_av.av_find_stream_info(format_context))

        nb_streams = format_context.contents.nb_streams
        streams = format_context.contents.streams

        # Determine which stream is the audio stream.
        for i in range(nb_streams):
            codec_type = streams[i].contents.codec.contents.codec_type
            if codec_type == _av.AVMEDIA_TYPE_AUDIO:
                # Remember this is the audio stream index.
                self.__audio_stream = i

                stream = streams[i]
                break

        # Find the codec to decode the audio.
        codec = _av.avcodec_find_decoder(stream.contents.codec.contents.codec_id)

        # Allocate space for the context for the codec.
        codec_context = _av.avcodec_alloc_context3(codec)

        # Copy the context.
        _av.avcodec_copy_context(codec_context, stream.contents.codec)

        av_dict = _av.POINTER(_av.AVDictionary)()

        # Get the codec and open a codec context from it.
        self._check(_av.avcodec_open2(codec_context, codec,
                                      _av.byref(av_dict)))

        _av.av_dict_free(_av.byref(av_dict))

        # Update the file info.
        self._rate = int(codec_context.contents.sample_rate)
        self._channels = int(codec_context.contents.channels)

        # Get the bit depth.
        depth = _av.av_get_bytes_per_sample(codec_context.contents.sample_fmt)
        self._depth = depth * 8 if depth < 4 else 16

        # Use the sample format string to determine the depth and whether it is
        # signed.
        d_str = _av.av_get_sample_fmt_name(codec_context.contents.sample_fmt)
        d_str = d_str.decode()

        # Extract the signed and depth properties from the sample format
        # string.
        self._unsigned = 'u' in d_str.lower()

        # if 'flt' in d_str:
        #     self._floatp = True

        # self._unsigned = d_str[0].lower() == 'u'
        # self._depth = int(d_str[1:])

        self._sample_fmt = getattr(_av, 'AV_SAMPLE_FMT_%s%s' %
                                   ('U' if self._unsigned else 'S',
                                    self._depth))

        self._avr = self._get_avr(codec_context)

        self.__codec_context = codec_context

        # The file is now open.
        self._closed = False

        return format_context

    def _get_avr(self, codec_context):
        """ Return an allocated AVResampleContext.

        """

        avr = _av.avresample_alloc_context()
        if not avr:
            raise(Exception("Unable to allocate avresample context"))

        if codec_context.contents.channel_layout == 0:
            channel_layout = _av.AV_CH_LAYOUT_STEREO
        else:
            channel_layout = codec_context.contents.channel_layout

        _av.av_opt_set_int(avr, b"in_channel_layout", channel_layout, 0)
        _av.av_opt_set_int(avr, b"out_channel_layout", channel_layout, 0)
        _av.av_opt_set_int(avr, b"in_sample_fmt",
                           codec_context.contents.sample_fmt, 0)
        _av.av_opt_set_int(avr, b"out_sample_fmt", self._sample_fmt, 0)
        _av.av_opt_set_int(avr, b"in_sample_rate",
                           codec_context.contents.sample_rate, 0)
        _av.av_opt_set_int(avr, b"out_sample_rate",
                           codec_context.contents.sample_rate, 0)
        # _av.av_opt_set_int(avr, b"force_resampling", 1, 0)

        _av.avresample_open(avr)

        return avr

    @io_wrapper
    def read(self, size):
        """ read(size=None) -> Reads size amount of data and returns it.  If
        size is None read buffer_size of data.

        """

        # Only update the global data buffer.
        data = self._data

        # Create the packet.
        av_packet = _av.AVPacket()

        # Create and setup a frame to read the data into.
        frame = _av.avcodec_alloc_frame()
        _av.avcodec_get_frame_defaults(frame)

        # Create and setup a frame to read the data into.
        outframe = _av.avcodec_alloc_frame()
        _av.avcodec_get_frame_defaults(outframe)

        # Used to tell if we read a frame or not.
        got_frame = _av.c_int()

        # Seek before next read begins.
        if self._seek_pos > -1:
            self._check(_av.avformat_seek_file(self.__format_context, -1, 0,
                                               self._seek_pos, self._length,
                                               _av.SEEK_SET))
            _av.avcodec_flush_buffers(self.__codec_context)

            # Reset the seek so we don't continue seeking.
            self._seek_pos = -1

        while not data or len(data) < size:
            # Read the next frame breaking.
            if _av.av_read_frame(self.__format_context, av_packet) < 0:
                # If no data was read then we have reached the end of the
                # file so restart or exit.
                if self._loops != -1 and self._loop_count >= self._loops:
                    # Free the packet.
                    _av.av_free_packet(av_packet)

                    # Fill the data buffer with nothing so it will be a
                    # frame size for output.
                    if len(data) != 0:
                        data += b'\x00' * (size - len(data))
                else:
                    # Fill the buffer so we return the requested size.
                    data += b'\x00' * (size - len(data))

                    # Update the loop count and seek to the start.
                    self._loop_count += 1
                    self.seek(0)

                # Exit.
                break

            # If the packet read is not audio then skip it.
            if av_packet.stream_index != self.__audio_stream:
                _av.av_free_packet(av_packet)
                continue

            # Reset the frame, (I don't know if this is necessary).
            _av.avcodec_get_frame_defaults(frame)

            # Initialize the packet before using it but after decoding
            # into it, otherwise it could segfault when freed.
            _av.av_init_packet(av_packet)

            # Decode the data in the packet until there is no more.
            while av_packet.size > 0:
                # Decode the packet data.
                data_len = _av.avcodec_decode_audio4(self.__codec_context,
                                                     frame,
                                                     _av.byref(got_frame),
                                                     av_packet)

                # Exit loop if no data was decoded.
                if data_len < 0:
                    break

                # Don't finish the loop if we didn't get a frame.
                # if not got_frame:
                #     continue

                # Calculate the size of the decoded data.
                data_size = _av.av_samples_get_buffer_size(
                        frame.contents.linesize,
                        self.__codec_context.contents.channels,
                        frame.contents.nb_samples,
                        self.__codec_context.contents.sample_fmt, 1)

                # We decoded 'data_len' amount of data, so remove it from the
                # packet.
                av_packet.size -= data_len
                #av_packet.data = \
                        #_av.POINTER(_av.c_uint8).from_address(
                                #_av.addressof(av_packet.data) + data_len)
                _av.memmove(av_packet.data, av_packet.data, av_packet.size)

                # Append the decoded data to the buffer.
                # data += _av.string_at(output, out_linesize)
                data += self._resample(frame)

            # Free the packet.
            _av.av_free_packet(av_packet)

        # Free the frame and packet.
        _av.av_free(frame)
        _av.av_free_packet(av_packet)

        # Store extra data for next time.
        self._data = data[size:]

        # Make sure we return only the number of bytes requested.
        return data[:size]
    read.__annotations__ = {'size': int, 'return': bytes}

    def _resample(self, frame):
        """ Resample the data in frame and return a byte string of the result.

        """

        # Don't resample null data.
        if not frame.contents.linesize[0]:
            return b''

        output = _av.POINTER(_av.uint8_t)()
        out_linesize = _av.c_int()

        # Calculate how many resampled samples there will be.
        r_rnd = _av.av_rescale_rnd(_av.avresample_get_delay(self._avr) +
                                   frame.contents.nb_samples,
                                   self.__codec_context.contents.sample_rate,
                                   self.__codec_context.contents.sample_rate,
                                   _av.AV_ROUND_UP)
        out_samples = _av.avresample_available(self._avr) + r_rnd

        # Allocate a buffer large enough to hold the resampled data.
        _av.av_samples_alloc(_av.byref(output), _av.byref(out_linesize),
                             self.__codec_context.contents.channels,
                             out_samples, self._sample_fmt, 0)

        # Resample the data in the frame to match the settings in avr.
        _av.avresample_convert(self._avr, _av.byref(output), out_linesize,
                               out_samples, frame.contents.data,
                               frame.contents.linesize[0],
                               frame.contents.nb_samples)
        # print(_av.avresample_available(avr))
        # print(_av.avresample_read(avr, outframe.contents.data, frame.contents.nb_samples))

        # Get the bytes in the output buffer.
        data = _av.string_at(output, out_linesize)

        # Free the output buffer.
        _av.av_freep(_av.byref(output))

        return data

    def close(self):
        """ close -> Closes and cleans up.

        """

        if not self.closed:
            # Close and free the resample context.
            _av.avresample_free(self._avr)

            # Close the file and free all contexts.
            _av.avformat_free_context(self.__format_context)
            self.__format_context = None

            # Deinit the network if the file read was a network stream.
            if self.__network_stream:
                _av.avformat_network_deinit()

            # This file is closed.
            self._closed = True
