/*
 * Painel de monitoramento.
 *
 * Consome o fluxo de eventos do servidor (WebSocket) e o MJPEG anotado.
 * Tambem reproduz aqui o audio das locucoes -- enquanto o fone acoplado ao
 * prototipo nao existe, o navegador faz esse papel.
 */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    preview: $("preview"),
    videoEmpty: $("video-empty"),
    deviceSelect: $("device-select"),
    zone: $("zone"),
    zoneValue: $("zone-value"),
    zoneDistance: $("zone-distance"),
    volume: $("volume"),
    detections: $("detections"),
    log: $("log"),
    player: $("player"),
    audioToggle: $("chk-audio"),
    commandForm: $("command-form"),
    commandInput: $("command-input"),
    suggestions: $("suggestions"),
    metrics: {
      fps: $("m-fps"),
      inference: $("m-inference"),
      latency: $("m-latency"),
      objects: $("m-objects"),
      drops: $("m-drops"),
    },
    badges: {
      detector: $("badge-detector"),
      tts: $("badge-tts"),
      stt: $("badge-stt"),
      conn: $("badge-conn"),
    },
  };

  const ZONE_LABELS = { safe: "livre", warning: "atencao", critical: "risco imediato" };

  const SUGGESTIONS = [
    "o que tem na minha frente?",
    "onde esta a cadeira?",
    "quantas pessoas tem aqui?",
    "o caminho esta livre?",
    "aumentar volume",
    "pausar",
  ];

  const state = { deviceId: null, audioQueue: [], playing: false };

  /* ------------------------------------------------------------------ log */

  function addLog(kind, label, text) {
    const item = document.createElement("li");
    item.dataset.kind = kind;
    const time = new Date().toLocaleTimeString("pt-BR", { hour12: false });
    item.innerHTML =
      `<span class="time"></span><span class="kind"></span><span class="text"></span>`;
    item.children[0].textContent = time;
    item.children[1].textContent = label;
    item.children[2].textContent = text;
    el.log.prepend(item);
    while (el.log.childElementCount > 200) el.log.lastElementChild.remove();
  }

  /* ---------------------------------------------------------------- audio */

  // Fila serial: locucoes nunca se sobrepoem, igual ao comportamento do
  // dispositivo real, que tem um unico canal de audio.
  function enqueueAudio(url) {
    if (!url || !el.audioToggle.checked) return;
    state.audioQueue.push(url);
    if (!state.playing) playNext();
  }

  function playNext() {
    const url = state.audioQueue.shift();
    if (!url) {
      state.playing = false;
      return;
    }
    state.playing = true;
    el.player.src = url;
    el.player.play().catch(() => { /* autoplay bloqueado ate a 1a interacao */ });
  }

  el.player.addEventListener("ended", playNext);
  el.player.addEventListener("error", playNext);

  /* -------------------------------------------------------------- eventos */

  const HANDLERS = {
    "vision.detections": (event) => {
      const payload = event.payload;
      renderDetections(payload.detections || []);
      el.metrics.objects.textContent = (payload.detections || []).length;
      el.metrics.inference.textContent = Math.round(payload.inference_ms || 0);
      el.metrics.latency.textContent = Math.round(payload.end_to_end_ms || 0);
      el.metrics.fps.textContent = (payload.fps || 0).toFixed(1);
      showPreview();
    },
    "proximity.reading": (event) => {
      const payload = event.payload;
      if (payload.distance_m != null) {
        el.zoneDistance.textContent = `sonar: ${payload.distance_m.toFixed(2)} m`;
      } else {
        el.zoneDistance.textContent = "sonar: sem eco";
      }
      setZone(payload.zone);
    },
    "proximity.zone_changed": (event) => {
      setZone(event.payload.to);
      addLog(event.payload.to === "critical" ? "critical" : "alert", "zona",
             `${event.payload.from} -> ${event.payload.to}`);
    },
    "audio.speech_played": (event) => {
      const payload = event.payload;
      const kind = payload.priority >= 40 ? "critical" : payload.priority >= 30 ? "alert" : "speech";
      addLog(kind, "fala", payload.text);
      enqueueAudio(payload.audio_url);
    },
    "speech.command_received": (event) => {
      addLog("command", "comando", `"${event.payload.text}" -> ${event.payload.intent}`);
    },
    "speech.command_handled": (event) => {
      if (event.payload.reply) addLog("speech", "resposta", event.payload.reply);
    },
    "device.connected": (event) => {
      addLog("info", "dispositivo", `${event.payload.device_id} conectado`);
      refreshState();
    },
    "device.disconnected": (event) => {
      addLog("info", "dispositivo", `${event.payload.device_id} desconectado`);
      refreshState();
    },
    "device.button": (event) => {
      addLog("info", "botao", `${event.payload.button} -> ${event.payload.result}`);
      refreshState();
    },
    "system.error": (event) => addLog("critical", "erro", event.payload.error),
  };

  function setZone(zone) {
    if (!zone) return;
    el.zone.dataset.zone = zone;
    el.zoneValue.textContent = ZONE_LABELS[zone] || zone;
  }

  function renderDetections(detections) {
    el.detections.innerHTML = "";
    if (!detections.length) {
      const empty = document.createElement("li");
      empty.className = "muted";
      empty.textContent = "nenhum";
      el.detections.append(empty);
      return;
    }
    const DIRECTIONS = { left: "esquerda", center: "frente", right: "direita" };
    for (const detection of detections) {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = `${detection.label_pt} (${DIRECTIONS[detection.direction]})`;
      const distance = document.createElement("span");
      distance.className = "dist";
      distance.textContent = detection.distance_m != null
        ? `${detection.distance_m.toFixed(1)} m`
        : `${Math.round(detection.confidence * 100)}%`;
      item.append(name, distance);
      el.detections.append(item);
    }
  }

  function showPreview() {
    if (el.preview.src) return;
    const query = state.deviceId ? `?device_id=${encodeURIComponent(state.deviceId)}` : "";
    el.preview.src = `/api/v1/media/preview.mjpg${query}`;
    el.preview.hidden = false;
    el.videoEmpty.hidden = true;
  }

  /* ------------------------------------------------------------ conexao */

  function connectEvents() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/api/v1/events`);

    socket.onopen = () => {
      el.badges.conn.textContent = "conectado";
      el.badges.conn.dataset.state = "on";
    };
    socket.onclose = () => {
      el.badges.conn.textContent = "desconectado";
      el.badges.conn.dataset.state = "off";
      setTimeout(connectEvents, 2000);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      const handler = HANDLERS[event.type];
      if (handler) handler(event);
    };
  }

  /* -------------------------------------------------------------- estado */

  async function refreshState() {
    try {
      const response = await fetch("/api/v1/health");
      const health = await response.json();
      // Simulado em vermelho: as caixas nao vem da camera. Com o YOLO, mostra
      // pesos e dispositivo -- e o que se precisa conferir antes de um ensaio.
      const simulated = health.detector === "fake";
      el.badges.detector.textContent = simulated
        ? "detector: SIMULADO (caixas ficticias)"
        : `detector: ${health.detector_model || health.detector} em ${health.detector_device}`;
      el.badges.detector.dataset.state = simulated ? "off" : "on";
      el.badges.detector.title = health.detector_note || "";
      el.badges.tts.textContent = `tts: ${health.tts}`;
      el.badges.stt.textContent = `stt: ${health.stt}`;
      renderDevices(health.devices || []);
    } catch (error) {
      console.warn("estado indisponivel", error);
    }
  }

  function renderDevices(devices) {
    const previous = el.deviceSelect.value;
    el.deviceSelect.innerHTML = "";
    for (const device of devices) {
      const option = document.createElement("option");
      option.value = device.device_id;
      option.textContent = `${device.device_id}${device.online ? "" : " (offline)"}`;
      el.deviceSelect.append(option);
    }
    if (devices.length) {
      el.deviceSelect.value = previous || devices[0].device_id;
      state.deviceId = el.deviceSelect.value;
      const current = devices.find((d) => d.device_id === state.deviceId) || devices[0];
      el.volume.textContent = current.volume_step;
      el.metrics.drops.textContent = current.frames_dropped;
      setZone(current.zone);
    }
  }

  /* ------------------------------------------------------------ comandos */

  async function sendCommand(text) {
    if (!text.trim()) return;
    await fetch("/api/v1/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: state.deviceId, text, source: "dashboard" }),
    });
  }

  el.commandForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendCommand(el.commandInput.value);
    el.commandInput.value = "";
  });

  for (const suggestion of SUGGESTIONS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = suggestion;
    button.addEventListener("click", () => sendCommand(suggestion));
    el.suggestions.append(button);
  }

  $("btn-start").addEventListener("click", () => sendButton("start_stop"));
  $("btn-volup").addEventListener("click", () => sendButton("volume_up"));
  $("btn-voldown").addEventListener("click", () => sendButton("volume_down"));
  $("btn-clear").addEventListener("click", () => { el.log.innerHTML = ""; });

  el.deviceSelect.addEventListener("change", () => {
    state.deviceId = el.deviceSelect.value;
    el.preview.src = "";
    showPreview();
  });

  async function sendButton(button) {
    if (!state.deviceId) return;
    await fetch("/api/v1/ingest/button", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: state.deviceId, button, action: "press" }),
    });
  }

  connectEvents();
  refreshState();
  setInterval(refreshState, 4000);
})();
