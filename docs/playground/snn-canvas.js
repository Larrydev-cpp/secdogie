/**
 * Scientific 1,280-neuron spiking raster & population firing-rate canvas renderer.
 * Draws plain LIF neurons (0..n_plain-1) in cobalt and adaptive ALIF neurons
 * (n_plain..n-1) in teal, with a live reading-tick sweep and hover crosshair.
 */

export class SpikeRasterRenderer {
  constructor(canvas, onHover = null) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onHover = onHover;
    this.snn = null;
    this.hoverTick = -1;
    this.progress = 1;
    this.rafId = null;
    this.lang = 'zh';
    this._bindEvents();
  }

  _bindEvents() {
    this.canvas.addEventListener('mousemove', (ev) => {
      if (!this.snn || !this.snn.ticks) return;
      const rect = this.canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const plot = this._layout(rect.width, rect.height);
      if (x < plot.left || x > plot.left + plot.w) {
        if (this.hoverTick !== -1) {
          this.hoverTick = -1;
          this.draw();
          if (this.onHover) this.onHover(null);
        }
        return;
      }
      const ratio = (x - plot.left) / Math.max(1, plot.w);
      const tick = Math.min(this.snn.ticks - 1, Math.max(0, Math.floor(ratio * this.snn.ticks)));
      if (tick !== this.hoverTick) {
        this.hoverTick = tick;
        this.draw();
        if (this.onHover) {
          const ch = tick < this.snn.chars.length ? this.snn.chars[tick] : null;
          const rate = this.snn.rate_per_tick[tick] || 0;
          this.onHover({ tick, char: ch, rate, isSettle: tick >= this.snn.chars.length });
        }
      }
    });

    this.canvas.addEventListener('mouseleave', () => {
      if (this.hoverTick !== -1) {
        this.hoverTick = -1;
        this.draw();
        if (this.onHover) this.onHover(null);
      }
    });

    window.addEventListener('resize', () => {
      if (this.snn) this.draw();
    });
  }

  setLang(lang) {
    this.lang = lang;
    if (this.snn) this.draw();
  }

  setData(snn) {
    this.snn = snn;
    this.hoverTick = -1;
    if (this.rafId) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
    const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced || !snn || !snn.ticks) {
      this.progress = 1;
      this.draw();
      return;
    }
    this.progress = 0;
    const start = performance.now();
    const duration = 420;
    const step = (now) => {
      const t = Math.min(1, (now - start) / duration);
      // Cubic ease-out settling curve
      this.progress = 1 - Math.pow(1 - t, 3);
      this.draw();
      if (t < 1) {
        this.rafId = requestAnimationFrame(step);
      } else {
        this.rafId = null;
      }
    };
    this.rafId = requestAnimationFrame(step);
  }

  _layout(width, height) {
    const left = 56;
    const right = 18;
    const top = 26;
    const bottom = 28;
    const w = Math.max(120, width - left - right);
    const totalH = Math.max(160, height - top - bottom);
    const rasterH = Math.round(totalH * 0.72);
    const rateGap = 14;
    const rateTop = top + rasterH + rateGap;
    const rateH = Math.max(32, totalH - rasterH - rateGap);
    return { left, right, top, bottom, w, rasterH, rateTop, rateH };
  }

  draw() {
    const canvas = this.canvas;
    const ctx = this.ctx;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(320, Math.floor(rect.width || 640));
    const height = Math.max(240, Math.floor(rect.height || 300));
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
      canvas.width = width * dpr;
      canvas.height = height * dpr;
    }

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    // Warm Monet snow-linen canvas background
    ctx.fillStyle = '#FDFBF7';
    ctx.fillRect(0, 0, width, height);

    const p = this._layout(width, height);
    const snn = this.snn;

    if (!snn || !snn.ticks) {
      ctx.fillStyle = '#516B78';
      ctx.font = '13px "IBM Plex Mono", monospace';
      ctx.fillText(this.lang === 'zh' ? '输入一句话并点击分析以观察神经元脉冲' : 'Enter a sentence to observe neural spikes', p.left, p.top + 40);
      ctx.restore();
      return;
    }

    const totalTicks = Math.max(1, snn.ticks);
    const readTicks = Math.max(0, totalTicks - (snn.settle_ticks || 12));
    const maxTickShown = Math.max(1, Math.ceil(totalTicks * this.progress));
    const nTotal = Math.max(1, snn.neurons || 1280);
    const nPlain = snn.n_plain || 1024;
    const adaptiveY = p.top + (nPlain / nTotal) * p.rasterH;

    // Subtle alpine-mist settle-phase shading
    const settleX = p.left + (readTicks / totalTicks) * p.w;
    ctx.fillStyle = '#EEF4F8';
    ctx.fillRect(settleX, p.top, p.left + p.w - settleX, p.rasterH);
    ctx.fillRect(settleX, p.rateTop, p.left + p.w - settleX, p.rateH);

    // Subtle Giverny garden-moss adaptive band tint
    ctx.fillStyle = 'rgba(35, 97, 64, 0.05)';
    ctx.fillRect(p.left, adaptiveY, p.w, p.top + p.rasterH - adaptiveY);

    // Hairline borders for raster and rate plots
    ctx.strokeStyle = 'rgba(24, 52, 66, 0.16)';
    ctx.lineWidth = 1;
    ctx.strokeRect(p.left + 0.5, p.top + 0.5, p.w, p.rasterH);
    ctx.strokeRect(p.left + 0.5, p.rateTop + 0.5, p.w, p.rateH);

    // Horizontal separator between Plain LIF and Adaptive ALIF populations
    ctx.beginPath();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = 'rgba(24, 52, 66, 0.24)';
    ctx.moveTo(p.left, Math.round(adaptiveY) + 0.5);
    ctx.lineTo(p.left + p.w, Math.round(adaptiveY) + 0.5);
    ctx.stroke();

    // Vertical separator between Character Reading and Settle ticks
    if (readTicks > 0 && readTicks < totalTicks) {
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(30, 93, 140, 0.36)';
      ctx.moveTo(Math.round(settleX) + 0.5, p.top);
      ctx.lineTo(Math.round(settleX) + 0.5, p.rateTop + p.rateH);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Axis labels
    ctx.fillStyle = '#516B78';
    ctx.font = '11px "IBM Plex Mono", monospace';
    ctx.textAlign = 'right';
    ctx.fillText('LIF', p.left - 8, p.top + 14);
    ctx.fillText('1024', p.left - 8, Math.max(p.top + 28, adaptiveY - 5));
    ctx.fillStyle = '#236140';
    ctx.fillText('ALIF', p.left - 8, Math.min(p.top + p.rasterH - 4, adaptiveY + 14));
    ctx.fillStyle = '#516B78';
    ctx.fillText('Hz', p.left - 8, p.rateTop + 13);

    // Phase headers (prevent collision on short prompts / narrow viewports)
    ctx.textAlign = 'left';
    ctx.fillStyle = '#3C5664';
    const readLabel = this.lang === 'zh'
      ? `逐字读入 (${readTicks} ms)`
      : `Char ticks (${readTicks} ms)`;
    ctx.fillText(readLabel, p.left + 2, p.top - 9);
    const settleLabel = this.lang === 'zh'
      ? `静默结算 (${snn.settle_ticks} ms)`
      : `Settle (${snn.settle_ticks} ms)`;
    const readRight = p.left + 2 + ctx.measureText(readLabel).width + 12;
    const settleWidth = ctx.measureText(settleLabel).width;
    if (p.left + p.w - readRight >= settleWidth) {
      ctx.fillStyle = '#516B78';
      if (settleX + 5 >= readRight && p.left + p.w - (settleX + 5) >= settleWidth) {
        ctx.textAlign = 'left';
        ctx.fillText(settleLabel, settleX + 5, p.top - 9);
      } else {
        ctx.textAlign = 'right';
        ctx.fillText(settleLabel, p.left + p.w - 2, p.top - 9);
      }
    }

    // Plot individual spikes (Alpine Cerulean LIF & Garden Moss ALIF)
    const raster = snn.raster || [];
    for (let i = 0; i < raster.length; i += 1) {
      const [tick, neuron, isAdp] = raster[i];
      if (tick > maxTickShown) continue;
      const x = p.left + ((tick + 0.5) / totalTicks) * p.w;
      const y = p.top + ((neuron + 0.5) / nTotal) * p.rasterH;
      ctx.fillStyle = isAdp ? '#236140' : '#1E5D8C';
      ctx.fillRect(x - 1, y - 1.5, 2.2, 3.0);
    }

    // Population firing-rate curve
    const rates = snn.rate_per_tick || [];
    const maxRate = Math.max(1, ...rates);
    ctx.beginPath();
    let started = false;
    for (let t = 0; t < Math.min(rates.length, maxTickShown); t += 1) {
      const x = p.left + ((t + 0.5) / totalTicks) * p.w;
      const y = p.rateTop + p.rateH - 3 - (rates[t] / maxRate) * (p.rateH - 8);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    }
    ctx.strokeStyle = '#1E5D8C';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    // Subtle cerulean glaze fill under firing-rate curve
    if (started) {
      const lastT = Math.min(rates.length, maxTickShown) - 1;
      const lastX = p.left + ((lastT + 0.5) / totalTicks) * p.w;
      const firstX = p.left + (0.5 / totalTicks) * p.w;
      ctx.lineTo(lastX, p.rateTop + p.rateH);
      ctx.lineTo(firstX, p.rateTop + p.rateH);
      ctx.closePath();
      ctx.fillStyle = 'rgba(30, 93, 140, 0.10)';
      ctx.fill();
    }

    // X-axis tick labels
    ctx.fillStyle = '#516B78';
    ctx.font = '11px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillText('0 ms', p.left, height - 8);
    ctx.textAlign = 'center';
    ctx.fillText(`${Math.round(totalTicks / 2)} ms`, p.left + p.w / 2, height - 8);
    ctx.textAlign = 'right';
    ctx.fillText(`${totalTicks} ms`, p.left + p.w, height - 8);

    // Interactive hover crosshair
    if (this.hoverTick >= 0 && this.hoverTick < totalTicks) {
      const hx = p.left + ((this.hoverTick + 0.5) / totalTicks) * p.w;
      ctx.beginPath();
      ctx.strokeStyle = '#152730';
      ctx.lineWidth = 1;
      ctx.moveTo(Math.round(hx) + 0.5, p.top);
      ctx.lineTo(Math.round(hx) + 0.5, p.rateTop + p.rateH);
      ctx.stroke();
    }

    ctx.restore();
  }
}
