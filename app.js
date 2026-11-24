const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtDay = d => new Date(d + 'T00:00').toLocaleDateString('en-IN', {weekday: 'short', day: 'numeric', month: 'short'});

/* ---------- dashboard ---------- */
let flashId = null, lastSlots = new Set();
async function loadDoctors() {
  const docs = await (await fetch('/api/doctors')).json();
  const now = new Set(docs.flatMap(d => d.slots.map(s => s.id)));
  $('#doctors').innerHTML = docs.map(d => {
    const byDay = {};
    d.slots.forEach(s => (byDay[s.date] ||= []).push(s));
    return `<article class="doc"><header><b>${esc(d.name)}</b><span>${esc(d.speciality)}</span></header>` +
      Object.entries(byDay).map(([day, ss]) => `<div class="day"><i>${fmtDay(day)}</i>${ss.map(s => `<span class="chip">${s.time}</span>`).join('')}</div>`).join('') + `</article>`;
  }).join('');
  lastSlots = now;
}
async function loadAppts() {
  const list = await (await fetch('/api/appointments')).json();
  $('#appts').innerHTML = list.length ? list.map(a => `
    <div class="row ${a.status} ${a.id === flashId ? 'flash' : ''}">
      <b>${esc(a.patient || 'Caller')}</b><small>${a.status === 'booked' ? esc(a.time) : 'Cancelled'}</small>
      <small>${esc(a.doctor)} · ${fmtDay(a.date)}</small><small>${esc(a.phone)}</small></div>`).join('')
    : '<p class="empty">No appointments yet. Start a call to book one.</p>';
}
const refresh = () => Promise.all([loadDoctors(), loadAppts()]);
refresh();

/* ---------- call ---------- */
let ws, ctx, micStream, node, analyserIn, analyserOut, outGain, nextT = 0, sources = [], muted = false, timerId, raf, startedAt, live = false, token = 0;
const IN = 16000, OUT = 24000, levels = Array(48).fill(0);
const WORKLET = `class P extends AudioWorkletProcessor{process(i){const c=i[0][0];if(c){const o=new Int16Array(c.length);for(let k=0;k<c.length;k++){const s=Math.max(-1,Math.min(1,c[k]));o[k]=s<0?s*32768:s*32767}this.port.postMessage(o.buffer,[o.buffer])}return true}}registerProcessor('pcm',P)`;

function tone(freq, t0, dur, vol = .08) {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.frequency.value = freq; g.gain.setValueAtTime(vol, ctx.currentTime + t0); g.gain.setValueAtTime(0, ctx.currentTime + t0 + dur);
  o.connect(g).connect(ctx.destination); o.start(ctx.currentTime + t0); o.stop(ctx.currentTime + t0 + dur);
}
const ring = () => { tone(440, 0, .8); tone(480, 0, .8); tone(440, 1.2, .8); tone(480, 1.2, .8); };
const hangTone = () => { tone(480, 0, .18); tone(380, .22, .18); tone(300, .44, .3); };

function setStatus(t) { $('#status').textContent = t; }
function bubble(role, text) {
  const p = document.createElement('p'); p.className = role; p.textContent = text;
  $('#log').append(p); p.scrollIntoView({block: 'end'});
}
function showCard(html) { const c = $('#card'); c.innerHTML = html; c.hidden = false; clearTimeout(c.t); c.t = setTimeout(() => c.hidden = true, 7000); }

async function startCall() {
  const my = ++token;
  $('#tryCall').disabled = true; $('#phone').hidden = false; $('#phone').className = 'phone ringing';
  $('#log').innerHTML = ''; $('#card').hidden = true; $('#timer').textContent = '00:00'; setStatus('Calling…'); muted = false; $('#mute').setAttribute('aria-pressed', 'false');
  ctx = new AudioContext({sampleRate: IN});
  try { micStream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, channelCount: 1}}); }
  catch { setStatus('Microphone blocked. Allow access and retry.'); return finish(0); }
  ring();
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], {type: 'application/javascript'})));
  const src = ctx.createMediaStreamSource(micStream);
  node = new AudioWorkletNode(ctx, 'pcm');
  analyserIn = ctx.createAnalyser(); analyserOut = ctx.createAnalyser(); analyserIn.fftSize = analyserOut.fftSize = 512;
  outGain = ctx.createGain(); outGain.connect(analyserOut); analyserOut.connect(ctx.destination);
  src.connect(analyserIn); src.connect(node);
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.binaryType = 'arraybuffer';
  node.port.onmessage = e => { if (live && !muted && ws.readyState === 1) ws.send(e.data); };
  ws.onmessage = e => typeof e.data === 'string' ? onEvent(JSON.parse(e.data)) : play(e.data);
  ws.onclose = () => live && endCall();
  ws.onerror = () => { setStatus('Server unreachable'); };
  await new Promise(r => setTimeout(r, 2400));           // let it ring
  if (my !== token) return;
  live = true; startedAt = Date.now();
  $('#phone').className = 'phone'; setStatus('Connected');
  timerId = setInterval(() => { const s = Math.floor((Date.now() - startedAt) / 1000); $('#timer').textContent = String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); }, 500);
  draw();
}
function play(buf) {
  if (!live) return;
  const pcm = new Int16Array(buf), f = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768;
  const ab = ctx.createBuffer(1, f.length, OUT); ab.copyToChannel(f, 0);
  const s = ctx.createBufferSource(); s.buffer = ab; s.connect(outGain);
  nextT = Math.max(nextT, ctx.currentTime + .03); s.start(nextT); nextT += ab.duration;
  sources.push(s); s.onended = () => sources = sources.filter(x => x !== s);
}
function onEvent(m) {
  if (m.type === 'interrupt') { sources.forEach(s => { try { s.stop(); } catch {} }); sources = []; nextT = 0; }
  else if (m.type === 'transcript') bubble(m.role, m.text);
  else if (m.type === 'booked' || m.type === 'cancelled') {
    const a = m.appointment, ok = m.type === 'booked';
    flashId = a.id; refresh();
    showCard(`<b>${ok ? 'Booked' : 'Cancelled'}</b><small>${esc(a.doctor)} · ${fmtDay(a.date)}, ${esc(a.time)}</small><small>${esc(a.patient)} · ${esc(a.phone)}</small>`);
  }
}
const rms = an => { const d = new Uint8Array(an.fftSize); an.getByteTimeDomainData(d); let s = 0; for (const v of d) s += ((v - 128) / 128) ** 2; return Math.sqrt(s / d.length); };
function draw() {
  const c = $('#wave'), g = c.getContext('2d'), W = c.width, H = c.height;
  const mic = muted ? 0 : rms(analyserIn), bot = rms(analyserOut), botLoud = bot > mic;
  levels.push(Math.min(1, Math.max(mic, bot) * 5)); levels.shift();
  g.clearRect(0, 0, W, H); const w = W / levels.length;
  levels.forEach((v, i) => { const h = 4 + v * (H - 8); g.fillStyle = botLoud ? '#3cc2bd' : '#e8f0f0'; g.globalAlpha = .25 + .75 * (i / levels.length);
    g.beginPath(); g.roundRect(i * w + 2, (H - h) / 2, w - 5, h, 4); g.fill(); });
  raf = requestAnimationFrame(draw);
}
function endCall() { if (!live && $('#phone').hidden) return; finish(1400); }
function finish(wait) {
  token++; const was = live; live = false;
  clearInterval(timerId); cancelAnimationFrame(raf);
  try { ws && ws.close(); } catch {}
  micStream && micStream.getTracks().forEach(t => t.stop());
  sources.forEach(s => { try { s.stop(); } catch {} }); sources = []; nextT = 0;
  if (was && ctx) hangTone();
  $('#phone').className = 'phone'; if (was) setStatus('Call ended');
  setTimeout(() => { $('#phone').hidden = true; $('#tryCall').disabled = false; ctx && ctx.close(); refresh(); }, wait);
}
$('#tryCall').onclick = startCall;
$('#end').onclick = () => finish(1400);
$('#mute').onclick = e => { muted = !muted; e.currentTarget.setAttribute('aria-pressed', muted); };
