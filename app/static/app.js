const result = document.querySelector('#result');
const status = document.querySelector('#status');
const loading = document.querySelector('#loading-template');

function setStatus(message, kind = '') {
  status.textContent = message;
  status.className = `status ${kind}`;
}

function showLoading() {
  result.className = 'result';
  result.replaceChildren(loading.content.cloneNode(true));
}

function text(value) { return value == null ? '' : String(value); }
function escapeHtml(value) { return text(value).replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char])); }

function showError(message) {
  result.className = 'result';
  result.innerHTML = `<div class="result-content"><p class="eyebrow">Request failed</p><h3>Could not complete that request</h3><p class="answer">${escapeHtml(message)}</p></div>`;
  setStatus('Needs attention', 'error');
}

function showIngest(data, label) {
  const rows = Object.entries(data).map(([key, value]) => `${key}: ${text(value)}`).join('\n');
  result.className = 'result';
  result.innerHTML = `<div class="result-content"><p class="eyebrow">${escapeHtml(label)} complete</p><h3>Filing is ready to query</h3><div class="evidence"><pre>${escapeHtml(rows)}</pre></div></div>`;
  setStatus('Ready', '');
}

async function traceHtml(traceId) {
  if (!traceId) return '';
  try {
    const trace = await request(`/traces/${encodeURIComponent(traceId)}`);
    const events = trace.events.map(event => `<details class="trace-event"><summary><span>${String(event.sequence).padStart(2, '0')}</span>${escapeHtml(event.stage)}</summary><pre>${escapeHtml(JSON.stringify(event.payload, null, 2))}</pre></details>`).join('');
    return `<details class="trace" open><summary>Execution trace <code>${escapeHtml(trace.id)}</code></summary><p>Stored internally in Postgres. SQL is parameterized; API keys and vector values are redacted.</p>${events}</details>`;
  } catch {
    return `<p class="trace-unavailable">Trace ID: ${escapeHtml(traceId)} (events could not be loaded)</p>`;
  }
}

async function showAnswer(data) {
  const structured = data.structured_evidence?.length ? escapeHtml(JSON.stringify(data.structured_evidence, null, 2)) : 'No structured evidence used.';
  const narrative = data.narrative_evidence?.length
    ? data.narrative_evidence.map(chunk => `<div class="chunk"><strong>${escapeHtml(chunk.section)} · ${escapeHtml(chunk.fiscal_period)}</strong><p>${escapeHtml(chunk.text)}</p></div>`).join('')
    : '<p>No narrative evidence used.</p>';
  result.className = 'result';
  const trace = await traceHtml(data.trace_id);
  result.innerHTML = `<div class="result-content"><div class="result-top"><div><p class="eyebrow">Answer</p><h3>Filing evidence</h3></div><span class="intent">${escapeHtml(data.intent)}</span></div><p class="answer">${escapeHtml(data.answer)}</p><div class="evidence-grid"><article class="evidence"><h4>Structured SQL evidence</h4><pre>${structured}</pre></article><article class="evidence"><h4>Judge-ranked narrative evidence</h4>${narrative}</article></div>${trace}</div>`;
  setStatus('Ready', '');
}

async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body;
}

function bindForm(selector, action) {
  document.querySelector(selector).addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true; setStatus('Working…', 'busy'); showLoading();
    try { await action(new FormData(form)); } catch (error) { showError(error.message); }
    finally { button.disabled = false; }
  });
}

bindForm('#sec-form', async data => {
  const payload = Object.fromEntries(data.entries());
  if (!payload.accession_number) delete payload.accession_number;
  const response = await request('/ingest/sec', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
  showIngest(response, 'SEC ingestion');
});

bindForm('#pdf-form', async data => showIngest(await request('/ingest/pdf/upload', {method:'POST', body:data}), 'PDF ingestion'));
bindForm('#ask-form', async data => showAnswer(await request('/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(Object.fromEntries(data.entries()))})));

document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  document.querySelector('#ask-form textarea').value = button.dataset.question;
  document.querySelector('#ask-form textarea').focus();
}));
