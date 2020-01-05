#!/usr/bin/python
# -*- coding: utf-8 -*-
#  Copyright (C) 2019 Sascha Montellese <montellese@kodi.tv>
#
#  SPDX-License-Identifier: GPL-2.0-or-later
#  See LICENSES/README.md for more information.
#
import threading

import xbmc
import xbmcmediaimport

from plex.api import Api
from plex.constants import PLEX_PROTOCOL, PLEX_PLAYER_PLAYING, PLEX_PLAYER_PAUSED, PLEX_PLAYER_STOPPED
from plex.server import Server

from lib.utils import log, mediaProvider2str

REPORTING_INTERVAL = 5 # seconds

class Player(xbmc.Player):
    def __init__(self):
        '''Initializes the player'''
        super(xbmc.Player, self).__init__()

        self._providers = {}
        self._monitor = xbmc.Monitor()
        self._lock = threading.Lock()

    def AddProvider(self, mediaProvider):
        '''Adds a media provider to the player'''
        if not mediaProvider:
            raise ValueError('invalid mediaProvider')

        self._providers[mediaProvider.getIdentifier()] = mediaProvider

    def RemoveProvider(self, mediaProvider):
        '''Removes the associated media provider'''
        if not mediaProvider:
            raise ValueError('invalid mediaProvider')

        del self._providers[mediaProvider.getIdentifier()]

    def onPlayBackStarted(self):
        '''Triggered when xbmc.Player is started'''
        self._reset()
        self._file = self.getPlayingFile()

    def onAVStarted(self):
        '''Triggered when playback actually starts'''
        self._startPlayback()
        self._syncPlaybackState(time=0, state=PLEX_PLAYER_PLAYING)

    def onPlayBackSeek(self, time, seekOffset):
        '''Triggered when seeking.'''
        self._syncPlaybackState(time=self._getPlayingTime())

    def onPlayBackSeekChapter(self, chapter):
        '''Triggered when seeking chapters.'''
        self._syncPlaybackState(time=self._getPlayingTime())

    def onPlayBackPaused(self):
        '''Triggered when playback is paused.'''
        self._syncPlaybackState(time=self._getPlayingTime(), state=PLEX_PLAYER_PAUSED)

    def onPlayBackResumed(self):
        '''Triggered when playback is resumed after a pause'''
        self._syncPlaybackState(time=self._getPlayingTime(), state=PLEX_PLAYER_PLAYING)

    def onPlayBackStopped(self):
        '''Triggered when playback is stopped'''
        self._syncPlaybackState(state=PLEX_PLAYER_STOPPED)
        self.onPlayBackEnded()

    def onPlayBackEnded(self):
        '''Triggered when playback ends. Resets the player state and inherently kills the reporting loop'''
        self._reset()

    def _startPlayback(self):
        '''Identifies the item (if from Plex) and initializes the player state'''
        if not self._file:
            return

        if not self.isPlayingVideo():
            return

        videoInfoTag = self.getVideoInfoTag()
        if not videoInfoTag:
            return

        itemId = videoInfoTag.getUniqueID(PLEX_PROTOCOL)
        if not itemId:
            return

        if not itemId.isdigit():
            log('invalid item id {} (non digit). Kodi will not report playback state to Plex Media Server' \
                .format(matchingItems[0]), xbmc.LOGERROR)

        self._itemId = int(itemId)

        for mediaProvider in self._providers.values():
            importedItems = xbmcmediaimport.getImportedItemsByProvider(mediaProvider)
            matchingItems = [ importedItem for importedItem in importedItems \
                if importedItem.getVideoInfoTag() and importedItem.getVideoInfoTag().getUniqueID(PLEX_PROTOCOL) == str(self._itemId) ]
            if not matchingItems:
                continue

            if len(matchingItems) > 1:
                log('multiple items imported from {} match the imported Plex item {} playing from {}' \
                    .format(mediaProvider2str(mediaProvider), self._itemId, self._file), xbmc.LOGWARNING)

            self._mediaProvider = mediaProvider
            break

        if self._mediaProvider:
            # save item
            plexServer = Server(self._mediaProvider)
            self._item = Api.getPlexItemDetails(
                plexServer.PlexServer(),
                self._itemId,
                Api.getPlexMediaClassFromMediaType(videoInfoTag.getMediaType())
            )
            self._duration = int(self.getTotalTime() * 1000)

            # start playback monitoring thread
            if not self._playback_monitor or not self._playback_monitor.isAlive():
                self._playback_monitor = threading.Thread(
                    target=self._monitor_playback,
                    name='PLEX:PLAYERMONITOR'
                )
                self._playback_monitor.start()

        else:
            self._reset()


    def _syncPlaybackState(self, state=None, time=None):
        '''Syncs last available state and publishes to PMS'''
        # either update state or time
        if not state and not time:
            return

        # sane check
        if not self._item:
            return

        with self._lock:
            if state:
                self._last_state['state'] = state

            if time is not None:
                self._last_state['time'] = int(time)

            # Send update to PMS
            if self._last_state.get('time') is not None and self._last_state.get('state'):
                self._item.updateTimeline(
                    self._last_state['time'],
                    state=self._last_state['state'],
                    duration=self._duration
                )


    def _monitor_playback(self):
        '''Monitor loop that reports current playback state to PMS'''
        while not self._monitor.abortRequested() and self._item is not None:
            self._monitor.waitForAbort(REPORTING_INTERVAL)
            if self._monitor.abortRequested() or not self._item:
                break
            self._syncPlaybackState(time=self._getPlayingTime())


    def _getPlayingTime(self):
        '''Gets current xbmc.Player time in miliseconds'''
        return int(self.getTime() * 1000)

    def _reset(self):
        '''Resets player member variables to default'''
        # Player item
        self._file = None
        self._item = None
        self._itemId = None
        self._mediaProvider = None
        self._duration = None
        # Player state
        self._playback_monitor = None
        self._last_state = {'time': None, 'state': None}
