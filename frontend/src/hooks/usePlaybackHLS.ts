import { useCallback, useEffect, useRef, useState } from 'react';
import Hls from 'hls.js';
import { authService } from '../services/authService';
import { withAuthQuery } from '../lib/api';

export function usePlaybackHLS(playlistUrl: string | null, initialSeek: number | null = null) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const destroyHls = useCallback(() => {
    if (hlsRef.current) {
      hlsRef.current.destroy();
      hlsRef.current = null;
    }
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
    pendingSeekRef.current = null;
  }, []);

  const applySeekToVideo = useCallback((video: HTMLVideoElement, seconds: number) => {
    if (!Number.isFinite(seconds) || seconds < 0) return;
    const run = () => {
      const max = video.duration && Number.isFinite(video.duration) ? video.duration : seconds;
      video.currentTime = Math.max(0, Math.min(seconds, max));
      setCurrentTime(video.currentTime);
    };
    if (video.readyState >= 1) {
      run();
    } else {
      video.addEventListener('loadedmetadata', run, { once: true });
    }
  }, []);

  useEffect(() => {
    if (!playlistUrl) {
      destroyHls();
      setLoading(false);
      setError(null);
      setIsPlaying(false);
      setCurrentTime(0);
      setDuration(0);
      return;
    }

    const video = videoRef.current;
    if (!video) return;

    destroyHls();
    setLoading(true);
    setError(null);
    setIsPlaying(false);
    setCurrentTime(0);
    setDuration(0);

    const seekTarget =
      initialSeek != null && initialSeek > 0 ? initialSeek : null;
    pendingSeekRef.current = seekTarget;

    const url = withAuthQuery(`${playlistUrl}?_${Date.now()}`);

    if (Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        maxBufferLength: 30,
        maxMaxBufferLength: 60,
        manifestLoadingMaxRetry: 4,
        fragLoadingMaxRetry: 6,
        startPosition: seekTarget ?? -1,
        xhrSetup: (xhr) => {
          const userId = authService.getUserId();
          if (userId) {
            xhr.setRequestHeader('X-User-Id', userId);
          }
        },
      });
      hlsRef.current = hls;
      hls.loadSource(url);
      hls.attachMedia(video);

      const tryApplyPendingSeek = () => {
        const target = pendingSeekRef.current;
        if (target == null || target <= 0) return;
        applySeekToVideo(video, target);
        pendingSeekRef.current = null;
      };

      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        setLoading(false);
        tryApplyPendingSeek();
        video.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
      });

      hls.on(Hls.Events.LEVEL_LOADED, () => {
        tryApplyPendingSeek();
      });

      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
          return;
        }
        if (data.response?.code === 401) {
          setError('Authentication required — log in again');
        } else if (data.response?.code === 404) {
          setError('Recording file not found');
        } else {
          setError('Failed to load recording');
        }
        setLoading(false);
        setIsPlaying(false);
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      video.addEventListener(
        'loadedmetadata',
        () => {
          setLoading(false);
          if (seekTarget != null) {
            applySeekToVideo(video, seekTarget);
            pendingSeekRef.current = null;
          }
          video.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
        },
        { once: true },
      );
      video.addEventListener(
        'error',
        () => {
          setError('Failed to load recording');
          setLoading(false);
        },
        { once: true },
      );
    } else {
      setError('HLS playback not supported in this browser');
      setLoading(false);
    }

    return destroyHls;
  }, [playlistUrl, initialSeek, destroyHls, applySeekToVideo]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const onTimeUpdate = () => setCurrentTime(video.currentTime);
    const onDuration = () => setDuration(video.duration || 0);
    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onEnded = () => setIsPlaying(false);

    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('loadedmetadata', onDuration);
    video.addEventListener('durationchange', onDuration);
    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);

    return () => {
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('loadedmetadata', onDuration);
      video.removeEventListener('durationchange', onDuration);
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('ended', onEnded);
    };
  }, [playlistUrl]);

  const play = useCallback(() => {
    videoRef.current?.play().catch(() => {});
  }, []);

  const pause = useCallback(() => {
    videoRef.current?.pause();
  }, []);

  const togglePlayPause = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      play();
    } else {
      pause();
    }
  }, [play, pause]);

  const seek = useCallback((time: number) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(time)) return;
    applySeekToVideo(video, time);
    video.play().catch(() => {});
  }, [applySeekToVideo]);

  const setPlaybackRate = useCallback((rate: number) => {
    const video = videoRef.current;
    if (video) video.playbackRate = rate;
  }, []);

  return {
    videoRef,
    loading,
    error,
    isPlaying,
    currentTime,
    duration,
    play,
    pause,
    togglePlayPause,
    seek,
    setPlaybackRate,
  };
}
