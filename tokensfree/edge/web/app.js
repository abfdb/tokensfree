(function () {
  'use strict';

  var session = { token: '', base_url: '' };
  var currentController = null;
  var refreshTimer = null;

  function byId(id) { return document.getElementById(id); }

  function showError(message) {
    var banner = byId('error-banner');
    banner.textContent = message || '';
    banner.classList.toggle('hidden', !message);
  }

  function setBusy(busy) {
    byId('send-btn').disabled = busy;
    byId('stop-btn').disabled = !busy;
  }

  function addMessage(role, text) {
    var transcript = byId('transcript');
    var node = document.createElement('div');
    node.className = 'msg ' + role;
    node.textContent = text;
    transcript.appendChild(node);
    transcript.scrollTop = transcript.scrollHeight;
    return node;
  }

  function authHeaders(extra) {
    var headers = { 'Authorization': 'Bearer ' + session.token };
    if (extra) {
      Object.keys(extra).forEach(function (key) { headers[key] = extra[key]; });
    }
    return headers;
  }

  function parseSseBlock(block) {
    var lines = block.split('\n');
    for (var i = 0; i < lines.length; i += 1) {
      var line = lines[i];
      if (line.indexOf('data:') !== 0) continue;
      var payload = line.slice(5).trim();
      if (!payload) continue;
      if (payload === '[DONE]') return { done: true };
      try {
        return { chunk: JSON.parse(payload) };
      } catch (error) {
        return { invalid: true };
      }
    }
    return null;
  }

  async function streamChat(payload, bubble) {
    var controller = new AbortController();
    currentController = controller;
    var response = await fetch('/v1/chat/completions', {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    if (!response.ok) {
      var rawtext = await response.text();
      throw new Error(response.status + ' ' + rawtext.slice(0, 400));
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder('utf-8');
    var buffer = '';
    var text = '';
    while (true) {
      var step = await reader.read();
      if (step.done) break;
      buffer += decoder.decode(step.value, { stream: true });
      var boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        var block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        var event = parseSseBlock(block);
        if (event && event.done) {
          return text;
        }
        if (event && event.invalid) {
          throw new Error('网关返回了无法解析的 SSE 帧');
        }
        if (event && event.chunk) {
          var chunk = event.chunk;
          if (chunk.error) {
            throw new Error((chunk.error.code || 'error') + ': ' + (chunk.error.message || ''));
          }
          var choice = (chunk.choices || [])[0];
          var delta = (choice && choice.delta) || {};
          if (typeof delta.content === 'string' && delta.content) {
            text += delta.content;
            bubble.textContent = text;
          }
        }
        boundary = buffer.indexOf('\n\n');
      }
    }
    return text;
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (!session.token) {
      showError('尚未取得本地会话令牌，请刷新页面');
      return;
    }
    var prompt = byId('prompt').value.trim();
    if (!prompt) return;
    var model = byId('model-select').value || 'tokensfree-auto';
    var stream = byId('stream-toggle').checked;
    byId('prompt').value = '';
    addMessage('user', prompt);
    var bubble = addMessage('assistant', '…');
    showError('');
    setBusy(true);
    var payload = {
      model: model,
      messages: [{ role: 'user', content: prompt }],
      stream: stream
    };
    try {
      if (stream) {
        var text = await streamChat(payload, bubble);
        if (!text) bubble.textContent = '(空响应)';
        addMessage('meta', '流式结束 · ' + model + ' · request_id ' + '见响应头 x-request-id');
      } else {
        var response = await fetch('/v1/chat/completions', {
          method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(payload)
        });
        var body = await response.json().catch(function () { return {}; });
        if (!response.ok) {
          throw new Error((body.error && (body.error.code + ': ' + body.error.message)) || ('HTTP ' + response.status));
        }
        var choice = (body.choices || [])[0] || {};
        var message = choice.message || {};
        var content = typeof message.content === 'string' ? message.content : '';
        if (!content && message.tool_calls && message.tool_calls.length) {
          content = '[tool_calls] ' + message.tool_calls.map(function (call) {
            return (call.function || {}).name || 'tool';
          }).join(', ');
        }
        bubble.textContent = content || '(空响应)';
        addMessage('meta', model + ' · finish_reason ' + (choice.finish_reason || '-'));
      }
    } catch (error) {
      bubble.textContent = '(失败)';
      if (error && error.name === 'AbortError') {
        addMessage('meta', '已停止');
      } else {
        showError(String(error && error.message ? error.message : error));
      }
    } finally {
      currentController = null;
      setBusy(false);
    }
  }

  function renderTable(container, rows, render) {
    container.textContent = '';
    if (!rows.length) {
      var empty = document.createElement('div');
      empty.className = 'dim';
      empty.textContent = '无数据';
      container.appendChild(empty);
      return;
    }
    rows.forEach(function (row) {
      container.appendChild(render(row));
    });
  }

  function item(name, detail, state) {
    var node = document.createElement('div');
    node.className = 'item';
    var left = document.createElement('span');
    left.className = 'name';
    left.textContent = name;
    var right = document.createElement('span');
    right.className = 'state-' + state;
    right.textContent = detail;
    node.appendChild(left);
    node.appendChild(right);
    return node;
  }

  async function loadStatus() {
    try {
      var response = await fetch('/api/status', { headers: authHeaders() });
      var body = await response.json();
      if (!response.ok) throw new Error('status ' + response.status);
      var providers = body.providers || [];
      var accounts = body.accounts || [];
      var breakers = body.breakers || {};
      var simulation = body.simulation !== false;
      byId('version').textContent = 'v' + (body.version || '-');
      byId('uptime').textContent = '运行 ' + (body.uptime_s || 0) + 's';
      var badge = byId('mode-badge');
      badge.textContent = simulation ? 'MOCK 运行时' : '真实 provider';
      badge.className = 'badge ' + (simulation ? 'ok' : 'real');
      renderTable(byId('status-providers'), providers, function (provider) {
        var detail = provider.state === 'eligible'
          ? (provider.model || '-')
          : (provider.state + ' / ' + (provider.policy_reason || '-'));
        return item(provider.name, detail, provider.state === 'eligible' ? 'eligible' : 'blocked');
      });
      renderTable(byId('status-accounts'), accounts, function (account) {
        var detail = account.status + ' · 已计费 ' + (account.requests_charged || 0);
        return item(account.provider, detail, account.status);
      });
      var breakerRows = Object.keys(breakers).map(function (name) {
        return { name: name, state: breakers[name].state || 'unknown' };
      });
      renderTable(byId('status-breakers'), breakerRows, function (breaker) {
        return item(breaker.name, breaker.state, breaker.state);
      });
    } catch (error) {
      showError('读取状态失败：' + (error && error.message ? error.message : error));
    }
  }

  async function copyText(text, label) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        var area = document.createElement('textarea');
        area.value = text;
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        document.body.removeChild(area);
      }
      addMessage('meta', '已复制 ' + label);
    } catch (error) {
      showError('复制失败，请手动选择文本');
    }
  }

  async function boot() {
    try {
      var response = await fetch('/api/session');
      if (!response.ok) throw new Error('无法获取本地会话令牌 (' + response.status + ')');
      session = await response.json();
      byId('base-url').textContent = session.base_url;
      byId('token-masked').textContent = '••••••••';
      byId('copy-base').addEventListener('click', function () { copyText(session.base_url, 'Base URL'); });
      byId('copy-token').addEventListener('click', function () { copyText(session.token, '本地令牌'); });
      byId('toggle-token').addEventListener('click', function () {
        var masked = byId('token-masked');
        var hidden = masked.textContent.indexOf('•') === 0;
        masked.textContent = hidden ? session.token : '••••••••';
        byId('toggle-token').textContent = hidden ? '隐藏' : '显示';
      });
      var modelsResponse = await fetch('/v1/models', { headers: authHeaders() });
      var models = await modelsResponse.json();
      var select = byId('model-select');
      (models.data || []).forEach(function (model) {
        var option = document.createElement('option');
        option.value = model.id;
        var caps = (model.capabilities || []).join('/');
        option.textContent = model.id + (model.simulation ? ' · mock' : '') + (caps ? ' · ' + caps : '');
        select.appendChild(option);
      });
      var auto = document.createElement('option');
      auto.value = 'tokensfree-auto';
      auto.textContent = 'tokensfree-auto（自动选择）';
      select.insertBefore(auto, select.firstChild);
      select.value = 'tokensfree-auto';
      await loadStatus();
      await loadProfiles();
      refreshTimer = setInterval(function () {
        if (!document.hidden) loadStatus();
      }, 10000);
    } catch (error) {
      showError('控制台初始化失败：' + (error && error.message ? error.message : error) + '（请确认通过 http://127.0.0.1:<port>/ 打开）');
    }
  }

  var profiles = [];
  var editingProfileId = null;

  function value(id) { return byId(id).value; }
  function checked(id) { return byId(id).checked; }

  function makeMiniButton(label, handler) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "ghost mini";
    button.textContent = label;
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      handler();
    });
    return button;
  }

  function renderProfileRow(profile) {
    var row = document.createElement("div");
    row.className = "item";
    var left = document.createElement("div");
    var name = document.createElement("div");
    name.className = "name";
    name.textContent = profile.name + (profile.enabled ? "" : "（已停用）");
    var meta = document.createElement("div");
    meta.className = "dim";
    meta.textContent = [profile.base_url, profile.model, profile.cost_policy, profile.key_present ? "已存凭据" : "无凭据"].join(" · ");
    left.appendChild(name);
    left.appendChild(meta);
    var actions = document.createElement("div");
    actions.className = "actions";
    actions.appendChild(makeMiniButton("编辑", function () { showProfileForm(profile); }));
    actions.appendChild(makeMiniButton("测试", function () { testProfile(profile); }));
    actions.appendChild(makeMiniButton("删除", function () { deleteProfile(profile); }));
    row.appendChild(left);
    row.appendChild(actions);
    return row;
  }

  async function loadProfiles() {
    var hint = byId("profile-hint");
    var list = byId("profile-list");
    try {
      var response = await fetch("/api/profiles", { headers: authHeaders() });
      var body = await response.json();
      list.textContent = "";
      if (!body.ok || body.enabled === false) {
        profiles = [];
        byId("new-profile").disabled = true;
        hint.textContent = body.ok === false
          ? "通道管理不可用：" + (body.error || response.status)
          : "当前网关未以 --profiles 启动；请用桌面端“接入通道”或 --profiles 启动网关后再管理。";
        return;
      }
      byId("new-profile").disabled = false;
      profiles = body.profiles || [];
      hint.textContent = profiles.length
        ? "保存修改后需重启网关生效。"
        : "还没有通道。点击“新增”接入本机 Ollama / 远端 OpenAI 兼容服务。";
      profiles.forEach(function (profile) { list.appendChild(renderProfileRow(profile)); });
    } catch (error) {
      showError("读取通道失败：" + (error && error.message ? error.message : error));
    }
  }

  function showProfileForm(profile) {
    editingProfileId = profile ? profile.id : null;
    byId("profile-form-title").textContent = profile ? "编辑通道" : "新增通道";
    byId("profile-form-name").value = profile ? profile.name : "";
    byId("profile-form-url").value = profile ? profile.base_url : "http://127.0.0.1:11434/v1";
    byId("profile-form-model").value = profile ? profile.model : "";
    byId("profile-form-policy").value = profile ? profile.cost_policy : "zero_cost_local";
    var caps = (profile && profile.capabilities) || ["text", "stream"];
    byId("profile-cap-text").checked = caps.indexOf("text") !== -1;
    byId("profile-cap-stream").checked = caps.indexOf("stream") !== -1;
    byId("profile-cap-tools").checked = caps.indexOf("tools") !== -1;
    var evidence = (profile && profile.evidence) || {};
    byId("profile-form-snapshot").value = evidence.snapshot || "";
    byId("profile-form-expires").value = evidence.expires_at || "";
    byId("profile-form-key").value = "";
    byId("profile-form-enabled").checked = profile ? profile.enabled : true;
    byId("profile-form").classList.remove("hidden");
  }

  function hideProfileForm() {
    byId("profile-form").classList.add("hidden");
    editingProfileId = null;
  }

  async function saveProfile() {
    var capabilities = [];
    if (checked("profile-cap-text")) capabilities.push("text");
    if (checked("profile-cap-stream")) capabilities.push("stream");
    if (checked("profile-cap-tools")) capabilities.push("tools");
    var payload = {
      name: value("profile-form-name"),
      base_url: value("profile-form-url"),
      model: value("profile-form-model"),
      cost_policy: value("profile-form-policy"),
      capabilities: capabilities,
      evidence_snapshot: value("profile-form-snapshot"),
      evidence_expires_at: value("profile-form-expires"),
      enabled: checked("profile-form-enabled"),
      api_key: value("profile-form-key"),
    };
    if (editingProfileId) payload.id = editingProfileId;
    try {
      var response = await fetch("/api/profiles", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(payload),
      });
      var body = await response.json();
      if (!body.ok) { showError("保存失败：" + (body.error || response.status)); return; }
      showError("");
      hideProfileForm();
      await loadProfiles();
      addMessage("meta", "通道已保存；重启网关后生效");
    } catch (error) {
      showError("保存失败：" + (error && error.message ? error.message : error));
    }
  }

  async function deleteProfile(profile) {
    if (!confirm("删除通道 " + profile.name + "？同时会删除其凭据。")) return;
    try {
      var response = await fetch("/api/profiles/" + encodeURIComponent(profile.id), {
        method: "DELETE",
        headers: authHeaders(),
      });
      var body = await response.json();
      if (!body.ok) { showError("删除失败：" + (body.error || response.status)); return; }
      await loadProfiles();
    } catch (error) {
      showError("删除失败：" + (error && error.message ? error.message : error));
    }
  }

  async function testProfile(profile) {
    try {
      var response = await fetch("/api/profiles/" + encodeURIComponent(profile.id) + "/test", {
        method: "POST",
        headers: authHeaders(),
      });
      var body = await response.json();
      if (!body.ok) { showError("连接失败：" + (body.error || response.status)); return; }
      showError("");
      addMessage("meta", "通道 " + profile.name + " 可用：" + ((body.models || []).slice(0, 8).join(", ") || "(模型列表为空)") + "（" + body.latency_s + "s）");
    } catch (error) {
      showError("连接失败：" + (error && error.message ? error.message : error));
    }
  }

  byId("new-profile").addEventListener("click", function () { showProfileForm(null); });
  byId("refresh-profiles").addEventListener("click", loadProfiles);
  byId("profile-form-save").addEventListener("click", saveProfile);
  byId("profile-form-cancel").addEventListener("click", hideProfileForm);
  byId('chat-form').addEventListener('submit', sendMessage);
  byId('stop-btn').addEventListener('click', function () {
    if (currentController) currentController.abort();
  });
  byId('refresh-status').addEventListener('click', loadStatus);
  byId('prompt').addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      byId('chat-form').requestSubmit();
    }
  });
  window.addEventListener('beforeunload', function () {
    if (refreshTimer) clearInterval(refreshTimer);
  });
  boot();
}());
