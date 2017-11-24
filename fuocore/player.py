# -*- coding: utf-8 -*-

"""
    fuocore.player
    ~~~~~~~~~~~~~~

    fuocore media player.
"""

from abc import ABCMeta, abstractmethod
from enum import Enum
import logging
import random

from mpv import MPV, MpvEventID, MpvEventEndFile

from fuocore.dispatch import Signal
from fuocore.exceptions import NoBackendError

logger = logging.getLogger(__name__)

_backend = None


def set_backend(player):
    global _backend
    _backend = player


def get_backend():
    global _backend
    if _backend is None:
        raise NoBackendError
    return _backend


class State(Enum):
    """Player state"""

    stopped = 0
    paused = 1
    playing = 2


class PlaybackMode(Enum):
    one_loop = 0
    sequential = 1
    loop = 2
    random = 3


class Playlist(object):
    """player playlist provide a list of song model to play"""

    def __init__(self, songs=[], playback_mode=PlaybackMode.loop):
        """

        :param songs: list of :class:`fuocore.models.SongModel`
        :param playback_mode: :class:`fuocore.player.PlaybackMode`
        """
        self._last_index = None
        self._current_index = None
        self._songs = songs
        self._playback_mode = playback_mode

        # signals
        self.playback_mode_changed = Signal()
        self.song_changed = Signal()

    def __len__(self):
        return len(self._songs)

    def __getitem__(self, index):
        """overload [] operator"""
        return self._songs[index]

    def add(self, song):
        """insert a song after current song"""
        if song in self._songs:
            return

        if self._current_index is None:
            self._songs.append(song)
        else:
            self._songs.insert(self._current_index + 1, song)

    def remove(self, song):
        self._songs.remove(song)

    @property
    def current_song(self):
        if self._current_index is None:
            return None
        return self._songs[self._current_index]

    @current_song.setter
    def current_song(self, song):
        """change current song, emit song changed singal"""

        self._last_song = self.current_song

        if song is None:
            self._current_index = None
            return

        # add it to playlist if song not in playlist
        if song in self._songs:
            index = self._songs.index(song)
        else:
            if self._current_index is None:
                index = 0
            else:
                index = self._current_index + 1
            self._songs.insert(index, song)
        self._current_index = index
        self.song_changed.emit()

    @property
    def playback_mode(self):
        return self._playback_mode

    @playback_mode.setter
    def playback_mode(self, playback_mode):
        self._playback_mode = playback_mode
        self.playback_mode_changed.emit()

    def next(self):
        """advance to next song"""
        if not self._songs:
            return

        if self.current_song is None:
            self.current_song = self._songs[0]
            return

        if self.playback_mode == PlaybackMode.random:
            self.current_song = random.choice(range(0, len(self._songs)))
            return

        if self.playback_mode in (PlaybackMode.one_loop, PlaybackMode.loop):
            if self._current_index == len(self._songs) - 1:
                self.current_song = self._songs[0]
                return

        if self.playback_mode == PlaybackMode.sequential:
            if self._current_index == len(self._songs) - 1:
                self.current_song = None
                return

        self.current_song = self._songs[self._current_index + 1]

    def previous(self):
        """return to previous played song, if previous played song not exists,
        get the song before current song in playback mode order.
        """
        if not self._songs:
            return None

        if self._last_index is not None:
            self.current_song = self._songs[self._last_index]
            return

        if self._current_index is None:
            self.current_song = self._songs[0]
            return

        if self.playback_mode == PlaybackMode.random:
            index = random.choice(range(0, len(self._songs)))
        else:
            index = self._current_index - 1

        self.current_song = self._songs[index]


class AbstractPlayer(object, metaclass=ABCMeta):

    def __init__(self, playlist=Playlist(), **kwargs):
        self._position = 0
        self._volume = 100  # (0, 100)
        self._playlist = playlist
        self._state = State.stopped
        self._duration = None

        self.position_changed = Signal()
        self.state_changed = Signal()
        self.song_finished = Signal()
        self.duration_changed = Signal()
        self.media_changed = Signal()

    @property
    def state(self):
        """player state

        :return: :class:`fuocore.engine.State`
        """
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        self.state_changed.emit()

    @property
    def current_song(self):
        return self._playlist.current_song

    @property
    def playlist(self):
        """player playlist

        :return: :class:`fuocore.engine.Playlist`
        """
        return self._playlist

    @playlist.setter
    def playlist(self, playlist):
        self._playlist = playlist

    @property
    def position(self):
        """player position, the units is seconds"""
        return self._position

    @position.setter
    def position(self, position):
        self._position = position

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value):
        print('change volume')
        value = 0 if value < 0 else value
        value = 100 if value > 100 else value
        self._volume = value

    @property
    def duration(self):
        """player media duration, the units is seconds"""
        return self._duration

    @duration.setter
    def duration(self, value):
        if value is not None and value != self._duration:
            self._duration = value
            self.duration_changed.emit()

    @abstractmethod
    def play(self, url):
        """play media

        :param url: a local file absolute path, or a http url that refers to a
            media file
        """

    @abstractmethod
    def play_song(self, song):
        """play media by song model

        :param song: :class:`fuocore.models.SongModel`
        """

    @abstractmethod
    def resume(self):
        """play playback"""

    @abstractmethod
    def pause(self):
        """pause player"""

    @abstractmethod
    def toggle(self):
        """toggle player state"""

    @abstractmethod
    def stop(self):
        """stop player"""

    @abstractmethod
    def initialize(self):
        """"initialize player"""

    @abstractmethod
    def quit(self):
        """quit player, do some clean up here"""


class MpvPlayer(AbstractPlayer):
    """

    player will always play playlist current song. player will listening to
    playlist ``song_changed`` signal and change the current playback.
    """
    def __init__(self):
        super().__init__()
        self._mpv = MPV(ytdl=False,
                        input_default_bindings=True,
                        input_vo_keyboard=True)
        self._playlist = Playlist()
        self._playlist.song_changed.connect(self._on_song_changed)

    def initialize(self):
        self._mpv.observe_property(
            'time-pos',
            lambda name, position: self._on_position_changed(position)
        )
        self._mpv.observe_property(
            'duration',
            lambda name, duration: self._on_duration_changed(duration)
        )
        self._mpv.register_event_callback(lambda event: self._on_event(event))
        self.song_finished.connect(self._playlist.next)

    def quit(self):
        del self._mpv

    def play(self, url):
        logger.info('start play url:%s' % url)
        # clear playlist before play next song.
        # otherwise, mpv will seek to the last position and play
        self._mpv.playlist_clear()

        self._mpv.play(url)
        self.state = State.playing
        self.media_changed.emit()

    def play_song(self, song):
        if self.playlist.current_song is not None and \
                self.playlist.current_song == song:
            logger.warning('the song to be played is same as current song')
            return
        self._playlist.current_song = song
        self.play(song.url)

    def resume(self):
        self._mpv.pause = False
        self.state = State.playing

    def pause(self):
        self._mpv.pause = True
        self.state = State.paused

    def toggle(self):
        self._mpv.pause = not self._mpv.pause
        if self._mpv.pause:
            self.state = State.paused
        else:
            self.state = State.playing

    def stop(self):
        self._mpv.stop()
        self.state = State.stopped

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, position):
        self._mpv.seek(position, reference='absolute')
        self._position = position

    @AbstractPlayer.volume.setter
    def volume(self, value):
        super(MpvPlayer, MpvPlayer).volume.__set__(self, value)
        self._mpv.volume = self.volume

    def _on_position_changed(self, position):
        self._position = position
        self.position_changed.emit()

    def _on_duration_changed(self, duration):
        """listening to mpv duration change event"""
        logger.info('player receive duration changed signal')
        self.duration = duration

    def _on_song_changed(self):
        logger.info('player received song changed signal')
        if self._playlist.current_song is not None:
            logger.info('will play song: %s' % self._playlist.current_song)
            self.play(self._playlist.current_song.url)
        else:
            logger.info('playlist provide no song anymore.')

    def _on_event(self, event):
        if event['event_id'] == MpvEventID.END_FILE:
            reason = event['event']['reason']
            logger.info('current song finished. reason: %d' % reason)
            if reason != MpvEventEndFile.ABORTED:
                self.song_finished.emit()