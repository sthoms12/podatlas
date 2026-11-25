import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import type { ActivePlayback } from '../types/podatlas';
import { formatTime } from '../lib/format';

const SPEEDS = [1, 1.25, 1.5, 2];

interface AudioPlayerProps {
  playback: ActivePlayback | null;
  onRequestDefaultPlay: () => void;
}

export function AudioPlayer({ playback, onRequestDefaultPlay }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentSec, setCurrentSec] = useState(0);
  const [durationSec, setDurationSec] = useState(2880);
  const [speedIdx, setSpeedIdx] = useState(0);
  const [isPreviewMode, setIsPreviewMode] = useState(false);

  const speed = SPEEDS[speedIdx];

  // Respond whenever a new episode or timestamp jump is requested
  useEffect(() => {
    if (!playback) return;
    const { episode, startSec } = playback;
    setCurrentSec(startSec);
    setDurationSec(episode.durationSec || 2400);

    const audio = audioRef.current;
    if (episode.audioUrl && audio) {
      setIsPreviewMode(false);
      if (audio.src !== episode.audioUrl) {
        audio.src = episode.audioUrl;
      }
      audio.currentTime = startSec;
      audio.playbackRate = speed;
      audio
        .play()
        .then(() => setIsPlaying(true))
        .catch(() => {
          // Graceful fallback to preview timer if third-party RSS audio is blocked
          setIsPreviewMode(true);
          setIsPlaying(true);
        });
    } else {
      if (audio) {
        audio.pause();
        audio.removeAttribute('src');
      }
      setIsPreviewMode(true);
      setIsPlaying(true);
    }
  }, [playback]);

  // Sync playback rate on live audio element
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = speed;
    }
  }, [speed]);

  // Preview timer fallback when seed episodes do not have a live MP3 URL
  useEffect(() => {
    if (!isPreviewMode || !isPlaying || !playback) return;
    const id = window.setInterval(() => {
      setCurrentSec((prev) => {
        if (prev + 1 >= durationSec) {
          setIsPlaying(false);
          return durationSec;
        }
        return prev + 1;
      });
    }, 1000 / speed);
    return () => window.clearInterval(id);
  }, [isPreviewMode, isPlaying, playback, durationSec, speed]);

  const togglePlay = () => {
    if (!playback) {
      onRequestDefaultPlay();
      return;
    }
    if (isPreviewMode) {
      setIsPlaying((prev) => !prev);
      return;
    }
    const audio = audioRef.current;
    if (!audio) return;
    if (isPlaying) {
      audio.pause();
      setIsPlaying(false);
    } else {
      audio.play().then(() => setIsPlaying(true)).catch(() => setIsPreviewMode(true));
    }
  };

  const seekTo = (sec: number) => {
    const clamped = Math.max(0, Math.min(durationSec, sec));
    setCurrentSec(clamped);
    if (!isPreviewMode && audioRef.current) {
      audioRef.current.currentTime = clamped;
    }
  };

  const mentions = playback?.episode.mentions ?? [];

  return (
    <footer className="shrink-0 border-t border-slate-800 bg-slate-900/95 backdrop-blur px-4 py-2.5 flex items-center gap-4 z-30">
      <audio
        ref={audioRef}
        onTimeUpdate={(e) => {
          if (!isPreviewMode) setCurrentSec(Math.floor(e.currentTarget.currentTime));
        }}
        onLoadedMetadata={(e) => {
          if (e.currentTarget.duration && Number.isFinite(e.currentTarget.duration)) {
            setDurationSec(Math.floor(e.currentTarget.duration));
          }
        }}
        onEnded={() => setIsPlaying(false)}
      />

      {/* Play button & Episode title */}
      <div className="flex items-center gap-2.5 min-w-0 w-52 sm:w-64 shrink-0">
        <button
          type="button"
          onClick={togglePlay}
          title={isPlaying ? 'Pause' : 'Play'}
          className="w-9 h-9 rounded-full bg-sky-500 text-slate-950 flex items-center justify-center shrink-0 hover:bg-sky-400 transition shadow-sm cursor-pointer"
        >
          {isPlaying ? (
            <Pause className="w-4 h-4 fill-current" />
          ) : (
            <Play className="w-4 h-4 fill-current ml-0.5" />
          )}
        </button>
        <div className="min-w-0">
          <div className="text-xs font-semibold text-slate-100 truncate">
            {playback ? playback.episode.title : 'Select an episode to listen'}
          </div>
          <div className="text-[11px] text-slate-400 truncate">
            {playback
              ? `${playback.episode.show} · ${playback.cityName}, ${playback.country}`
              : 'Direct RSS audio streaming · $0 hosting'}
          </div>
        </div>
      </div>

      {/* Scrubber & Clickable Mention Markers */}
      <div className="flex-1 flex flex-col gap-1 min-w-0">
        <div className="flex items-center gap-2.5">
          <span className="text-[11px] font-mono tabular-nums text-slate-400 w-10 text-right">
            {formatTime(currentSec)}
          </span>
          <div className="relative flex-1 h-5 flex items-center">
            <input
              type="range"
              min={0}
              max={durationSec || 100}
              value={currentSec}
              onChange={(e) => seekTo(Number(e.target.value))}
              aria-label="Episode timeline"
              className="w-full accent-sky-400 cursor-pointer h-1.5"
            />
            <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 pointer-events-none h-2.5">
              {mentions.map((m, idx) => {
                const pct = durationSec > 0 ? Math.min(99, (m.sec / durationSec) * 100) : 0;
                return (
                  <button
                    key={`${m.label}-${idx}`}
                    type="button"
                    onClick={() => seekTo(m.sec)}
                    title={`${formatTime(m.sec)} — ${m.label}`}
                    style={{ left: `${pct}%` }}
                    className="pointer-events-auto absolute top-0 w-2 h-2.5 rounded-full bg-amber-400 hover:scale-125 transition -translate-x-1/2 cursor-pointer"
                  />
                );
              })}
            </div>
          </div>
          <span className="text-[11px] font-mono tabular-nums text-slate-400 w-10">
            {formatTime(durationSec)}
          </span>
        </div>
        <div className="text-[11px] text-slate-400 truncate pl-12">
          {playback
            ? playback.hintText ||
              (isPreviewMode
                ? 'Seed preview mode · Yellow markers jump to extracted Book/Place mentions'
                : 'Streaming direct RSS audio · Yellow markers jump to extracted mentions')
            : 'Click any timestamp badge in Episodes, Books, or Places to jump straight to that mention.'}
        </div>
      </div>

      {/* Skip & Speed Controls */}
      <div className="hidden md:flex items-center gap-1.5 shrink-0">
        <button
          type="button"
          onClick={() => seekTo(currentSec - 15)}
          className="px-2 py-1 rounded-md text-xs bg-slate-800 text-slate-300 hover:bg-slate-700 cursor-pointer"
        >
          -15s
        </button>
        <button
          type="button"
          onClick={() => seekTo(currentSec + 30)}
          className="px-2 py-1 rounded-md text-xs bg-slate-800 text-slate-300 hover:bg-slate-700 cursor-pointer"
        >
          +30s
        </button>
        <button
          type="button"
          onClick={() => setSpeedIdx((prev) => (prev + 1) % SPEEDS.length)}
          title="Playback speed"
          className="px-2 py-1 rounded-md text-xs font-mono bg-slate-800 text-sky-300 hover:bg-slate-700 cursor-pointer min-w-[44px]"
        >
          {speed}x
        </button>
      </div>
    </footer>
  );
}
