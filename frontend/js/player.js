// Reproductor de la vista de clase. Con MP4 usa <video>; sin video, TranscriptPlayer simula la
// reproducción sobre la transcripción con la misma interfaz (currentTime, play, pause, timeupdate…),
// así el seguimiento de rangos vistos funciona igual en ambos casos.
import { esc, fmtTime, qs } from './api.js';

const TICK_MS = 250;

class TranscriptPlayer extends EventTarget {
  constructor(container, duration, segments) {
    super();
    this.duration = duration;
    this.segments = segments;
    this._time = 0;
    this._rate = 1;
    this.paused = true;
    this.seeking = false;
    this._timer = null;
    container.innerHTML = `
      <div class="sol-tplayer" tabindex="0" aria-label="Reproductor de la clase (transcripción)">
        <div class="sol-tplayer-screen">
          <span class="sol-tplayer-tag"><i class="fa-solid fa-closed-captioning"></i> Clase sin video: la transcripción avanza como el video</span>
          <div class="sol-tplayer-speaker"></div>
          <p class="sol-tplayer-text" aria-live="polite"></p>
        </div>
        <div class="sol-tplayer-controls">
          <button type="button" class="sol-tplayer-play" aria-label="Reproducir"><i class="fa-solid fa-play"></i></button>
          <input type="range" min="0" max="${Math.floor(duration)}" step="1" value="0" aria-label="Posición de la clase">
          <span class="sol-tplayer-time">00:00 / ${fmtTime(duration)}</span>
          <select aria-label="Velocidad">
            ${[1, 1.5, 2, 5, 10, 30].map(r => `<option value="${r}">${r}×</option>`).join('')}
          </select>
        </div>
      </div>`;
    this.root = qs('.sol-tplayer', container);
    this.btn = qs('.sol-tplayer-play', container);
    this.range = qs('input[type=range]', container);
    this.timeEl = qs('.sol-tplayer-time', container);
    this.speakerEl = qs('.sol-tplayer-speaker', container);
    this.textEl = qs('.sol-tplayer-text', container);
    this.btn.addEventListener('click', () => (this.paused ? this.play() : this.pause()));
    this.range.addEventListener('input', () => { this.currentTime = Number(this.range.value); });
    qs('select', container).addEventListener('change', e => { this.playbackRate = Number(e.target.value); });
    this.root.addEventListener('keydown', e => {
      if (e.key === ' ' || e.key === 'k') { e.preventDefault(); this.paused ? this.play() : this.pause(); }
      if (e.key === 'ArrowRight') this.currentTime += 10;
      if (e.key === 'ArrowLeft') this.currentTime -= 10;
    });
    this._render();
  }

  get currentTime() { return this._time; }
  set currentTime(value) {
    this.seeking = true;
    this.dispatchEvent(new Event('seeking'));
    this._time = Math.min(this.duration, Math.max(0, value));
    this.seeking = false;
    this._render();
    this.dispatchEvent(new Event('seeked'));
    this.dispatchEvent(new Event('timeupdate'));
  }

  get playbackRate() { return this._rate; }
  set playbackRate(value) { this._rate = value; this.dispatchEvent(new Event('ratechange')); }

  play() {
    if (!this.paused) return Promise.resolve();
    if (this._time >= this.duration) this._time = 0;
    this.paused = false;
    this._timer = setInterval(() => this._tick(), TICK_MS);
    this._render();
    this.dispatchEvent(new Event('play'));
    return Promise.resolve();
  }

  pause() {
    if (this.paused) return;
    this.paused = true;
    clearInterval(this._timer);
    this._render();
    this.dispatchEvent(new Event('pause'));
  }

  _tick() {
    this._time = Math.min(this.duration, this._time + (TICK_MS / 1000) * this._rate);
    this._render();
    this.dispatchEvent(new Event('timeupdate'));
    if (this._time >= this.duration) { this.pause(); this.dispatchEvent(new Event('ended')); }
  }

  _segmentAt(t) {
    let current = null;
    for (const s of this.segments) { if (s.start <= t) current = s; else break; }
    return current;
  }

  _render() {
    this.range.value = Math.floor(this._time);
    this.timeEl.textContent = `${fmtTime(this._time)} / ${fmtTime(this.duration)}`;
    this.btn.innerHTML = `<i class="fa-solid fa-${this.paused ? 'play' : 'pause'}"></i>`;
    this.btn.setAttribute('aria-label', this.paused ? 'Reproducir' : 'Pausar');
    const seg = this._segmentAt(this._time);
    if (seg !== this._seg) {
      this._seg = seg;
      this.speakerEl.textContent = seg?.speaker || '';
      this.textEl.innerHTML = seg ? esc(seg.text) : 'Presiona reproducir para empezar.';
    }
  }
}

export function createPlayer(container, { videoUrl, duration, segments }) {
  if (videoUrl) {
    container.innerHTML = `<video class="sol-video" controls preload="metadata" playsinline src="${esc(videoUrl)}"></video>`;
    return qs('video', container);
  }
  return new TranscriptPlayer(container, duration, segments);
}

// Acumula los rangos realmente reproducidos (sin contar saltos).
export class WatchTracker {
  constructor(player) {
    this.player = player;
    this.pending = [];
    this.current = null;
    this.last = null;
    player.addEventListener('timeupdate', () => this._update());
    player.addEventListener('seeking', () => this._close());
    player.addEventListener('pause', () => this._close());
  }

  _update() {
    const t = this.player.currentTime;
    if (this.player.paused || this.player.seeking) { this.last = t; return; }
    const maxStep = Math.max(2, (this.player.playbackRate || 1) * 1.5);
    if (this.current && this.last != null && t >= this.last && t - this.last <= maxStep) {
      this.current[1] = t;
    } else {
      this._close();
      this.current = [t, t];
    }
    this.last = t;
  }

  _close() {
    if (this.current && this.current[1] - this.current[0] >= 1) this.pending.push(this.current);
    this.current = null;
    this.last = null;
  }

  // Devuelve los rangos acumulados desde el último envío (incluye el tramo en curso).
  take() {
    const out = [...this.pending];
    if (this.current && this.current[1] - this.current[0] >= 1) {
      out.push([...this.current]);
      this.current = [this.current[1], this.current[1]];
    }
    this.pending = [];
    return out.map(([a, b]) => [Math.round(a * 10) / 10, Math.round(b * 10) / 10]);
  }
}
