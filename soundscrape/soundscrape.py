#! /usr/bin/env python

import soundcloud
import requests
import sys
import argparse
import threading
import Queue

from mutagen.easyid3 import ID3, EasyID3 
from mutagen.mp3 import EasyMP3

from clint.textui import colored, puts, progress

# Please be nice with this!
CLIENT_ID = '22e566527758690e6feb2b5cb300cc43'
CLIENT_SECRET = '3a7815c3f9a82c3448ee4e7d3aa484a4'
MAGIC_CLIENT_ID = 'b45b1aa10f1ac2941910a7f0d10f8e28'

class ThreadPool:
    def __init__(self, n, func):
        self.workers = [threading.Thread(target=self._worker) for i in xrange(n)]
        for i in self.workers:
            i.daemon = True

        self.task_queue = Queue.Queue(64)
        self.func = func

        # Start workers
        self.running = True
        [x.start() for x in self.workers]

    def _worker(self):
        while(self.running):
            try:
                job, callback = self.task_queue.get(True, 2)
                res = self.func(job)
                self.task_queue.task_done()
                callback(res)
            except Queue.Empty:
                continue

    def submit(self, job, callback):
        self.task_queue.put((job, callback))

    def join(self):
        self.task_queue.join()
        self.running = False
        for i in self.workers:
            i.join()

def main():
    parser = argparse.ArgumentParser(description='SoundScrape. Scrape an artist from SoundCloud.\n')
    parser.add_argument('artist_url', metavar='U', type=str,
                   help='An artist\'s SoundCloud username or URL')
    parser.add_argument('-n', '--num-tracks', type=int, default=sys.maxint,
                        help='The number of tracks to download')
    parser.add_argument('-g', '--group', action='store_true',
                        help='Use if downloading tracks from a SoundCloud group')
    parser.add_argument('-t', '--track', type=str, default='',
                        help='The name of a specific track by an artist')
    parser.add_argument('-j', '--threads', type=int, default=1,
                        help="The number of concurrent downloads to allow")

    args = parser.parse_args()
    vargs = vars(args)
    if not any(vargs.values()):
        parser.error('Please supply an artist\'s username or URL!')

    artist_url = vargs['artist_url']
    track_permalink = vargs['track']
    one_track = False
    if 'soundcloud' not in artist_url.lower():
        if vargs['group']:
            artist_url = 'https://soundcloud.com/groups/' + artist_url.lower()
        elif len(track_permalink) > 0:
            one_track = True
            track_url = 'https://soundcloud.com/' + artist_url.lower() + '/' + track_permalink.lower()
        else:
            artist_url = 'https://soundcloud.com/' + artist_url.lower()

    pool = None if args.threads == 1 else ThreadPool(args.threads, download_file)
    client = soundcloud.Client(client_id=CLIENT_ID)
    if one_track:
        resolved = client.get('/resolve', url=track_url)
    else:
        resolved = client.get('/resolve', url=artist_url)

    if resolved.kind == 'artist':
        artist = resolved
        artist_id = artist.id
        tracks = client.get('/users/' + str(artist_id) + '/tracks')
    elif resolved.kind == 'playlist':
        tracks = resolved.tracks
    elif resolved.kind == 'track':
        tracks = [resolved]
    elif resolved.kind == 'group':
        group = resolved
        group_id = group.id
        tracks = client.get('/groups/' + str(group_id) + '/tracks')
    else:
        artist = resolved
        artist_id = artist.id
        tracks = client.get('/users/' + str(artist_id) + '/tracks')

    if one_track:
        num_tracks = 1;
    else:
        num_tracks = vargs['num_tracks']
    download_tracks(client, pool, tracks, num_tracks)

def download_tracks(client, pool, tracks, num_tracks=sys.maxint):
    try:
        for i, track in enumerate(tracks):
            # "Track" and "Resource" objects are actually different, 
            # even though they're the same. 
            if isinstance(track, soundcloud.resource.Resource):
                try:
                    t_track = {}
                    t_track['downloadable'] = track.downloadable
                    t_track['streamable'] = track.streamable
                    t_track['title'] = track.title
                    t_track['user'] = {'username': track.user['username']}
                    t_track['release_year'] = track.release
                    t_track['genre'] = track.genre
                    if track.downloadable:
                        t_track['stream_url'] = track.download_url
                    else:
                        if hasattr(track, 'stream_url'):
                            t_track['stream_url'] = track.stream_url
                        else:
                            t_track['direct'] = True
                            t_track['stream_url'] = 'https://api.soundcloud.com/tracks/' + \
                                str(track.id) + '/stream?client_id=' + MAGIC_CLIENT_ID
                    track = t_track
                except Exception, e:
                    puts(track.title.encode('utf-8') + colored.red(u' is not downloadable') + '.')
                    continue

            if i > num_tracks - 1:
                continue
            try:
                if not track.get('stream_url', False):
                    puts(track['title'].encode('utf-8')  + colored.red(u' is not downloadable') + '.')
                    continue
                else:
                    puts(colored.green(u"Downloading") + ": " + track['title'].encode('utf-8'))
                    if track.get('direct', False):
                        location = track['stream_url']
                    else:
                        stream = client.get(track['stream_url'], allow_redirects=False)
                        if hasattr(stream, 'location'):
                            location = stream.location
                        else:
                            location = stream.url

                    track_filename = track['user']['username'].replace('/', '-') + ' - ' + track['title'].replace('/', '-') + '.mp3'
                    if(pool != None):
                        def afterwards(pth, track):
                            tag_file(pth, 
                                    artist=track['user']['username'], 
                                    title=track['title'], 
                                    year=track['release_year'], 
                                    genre=track['genre'])
                            puts(colored.green(u"Finished") + ": " + track['title'].encode("utf-8"))
                        pool.submit((location, track_filename, track, False), lambda x: afterwards(*x))
                    else:
                        res, track_res = download_file((location, track_filename, track, True))
                        tag_file(res,
                                artist=track['user']['username'], 
                                title=track['title'], 
                                year=track['release_year'], 
                                genre=track['genre'])
            except Exception, e:
                puts(colored.red(u"Problem downloading ") + track['title'].encode('utf-8') )
                print e
    except KeyboardInterrupt, e:
        pass
    pool.join()

def download_file(job):
    url, path, track, do_progress = job
    r = requests.get(url, stream=True)
    try:
        with open(path, 'wb') as f:
            total_length = int(r.headers.get('content-length'))
            iterator = progress.bar(r.iter_content(chunk_size=1024), expected_size=(total_length/1024)+1) if do_progress else\
                r.iter_content(chunk_size=1024)
            for chunk in iterator:
                if chunk:
                    f.write(chunk)
                    f.flush()
    except Exception as e:
        puts(colored.red("Failed downloading ") + path)
        puts(colored.red(str(e)))

    return (path, track)

def tag_file(filename, artist, title, year, genre):
    try:
        audio = EasyMP3(filename)
        audio["artist"] = artist
        audio["title"] = title
        if year:
            audio["date"] = str(year)
        audio["genre"] = genre
        audio.save()
    except Exception, e:
        print e

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception, e:
        print e
