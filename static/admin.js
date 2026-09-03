/* Админ-панель: авторизация по токену, загрузка и удаление документов. */
(() => {
  const $ = (id) => document.getElementById(id);
  const KEY = 'subsoil_admin_token';

  const state = { token: sessionStorage.getItem(KEY) || '' };

  const setStatus = (node, text, kind) => {
    node.textContent = text;
    node.className = `status${kind ? ' ' + kind : ''}`;
  };

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers, {
      Authorization: `Bearer ${state.token}`,
    });
    const response = await fetch(path, Object.assign({}, options, { headers }));
    let data = null;
    try { data = await response.json(); } catch (_) { /* пустое тело */ }
    if (!response.ok) {
      throw new Error((data && data.detail) || `HTTP ${response.status}`);
    }
    return data;
  }

  function showPanel(visible) {
    $('panel').classList.toggle('hidden', !visible);
  }

  async function refresh() {
    const stats = await api('/api/admin/stats');
    $('stats').textContent =
      `Документов: ${stats.documents} · фрагментов: ${stats.chunks} · ` +
      `эмбеддинги: ${stats.embeddings_provider} · модель: ${stats.model}`;

    const docs = await api('/api/admin/documents');
    const body = $('docs-body');
    body.textContent = '';
    if (!docs.length) {
      setStatus($('docs-status'), 'База пуста — загрузите первый документ.', null);
      return;
    }
    setStatus($('docs-status'), '', null);
    docs.forEach((doc) => {
      const tr = document.createElement('tr');
      const title = document.createElement('td');
      title.textContent = doc.title;
      if (doc.note) {
        const note = document.createElement('div');
        note.className = 'muted';
        note.textContent = doc.note;
        title.appendChild(note);
      }
      const file = document.createElement('td');
      file.textContent = `${doc.filename} (${Math.round(doc.size_bytes / 1024)} КБ)`;
      const chunks = document.createElement('td');
      chunks.textContent = doc.chunks;
      const date = document.createElement('td');
      date.textContent = doc.uploaded_at.replace('T', ' ').replace('+00:00', ' UTC');
      const actions = document.createElement('td');
      const del = document.createElement('button');
      del.className = 'danger';
      del.textContent = 'Удалить';
      del.addEventListener('click', async () => {
        if (!confirm(`Удалить «${doc.title}» из базы?`)) return;
        try {
          await api(`/api/admin/documents/${doc.id}`, { method: 'DELETE' });
          await refresh();
        } catch (err) {
          setStatus($('docs-status'), err.message, 'err');
        }
      });
      actions.appendChild(del);
      [title, file, chunks, date, actions].forEach((cell) => tr.appendChild(cell));
      body.appendChild(tr);
    });
  }

  async function login() {
    state.token = $('token').value.trim();
    if (!state.token) {
      setStatus($('auth-status'), 'Введите токен.', 'err');
      return;
    }
    try {
      await refresh();
      sessionStorage.setItem(KEY, state.token);
      setStatus($('auth-status'), 'Доступ разрешён.', 'ok');
      showPanel(true);
    } catch (err) {
      showPanel(false);
      setStatus($('auth-status'), err.message, 'err');
    }
  }

  $('login').addEventListener('click', login);
  $('token').addEventListener('keydown', (e) => { if (e.key === 'Enter') login(); });
  $('logout').addEventListener('click', () => {
    sessionStorage.removeItem(KEY);
    state.token = '';
    $('token').value = '';
    showPanel(false);
    setStatus($('auth-status'), 'Вы вышли.', null);
  });

  $('upload-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const file = $('file').files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    form.append('title', $('title').value.trim());
    form.append('note', $('note').value.trim());
    form.append('replace', $('replace').checked ? 'true' : 'false');

    $('upload-btn').disabled = true;
    setStatus($('upload-status'), 'Индексирую документ, это может занять минуту…', null);
    try {
      const result = await api('/api/admin/documents', { method: 'POST', body: form });
      setStatus(
        $('upload-status'),
        `${result.replaced ? 'Обновлён' : 'Загружен'} «${result.document.title}»: ` +
        `${result.document.chunks} фрагментов.`,
        'ok'
      );
      $('upload-form').reset();
      await refresh();
    } catch (err) {
      setStatus($('upload-status'), err.message, 'err');
    } finally {
      $('upload-btn').disabled = false;
    }
  });

  $('probe-btn').addEventListener('click', async () => {
    const question = $('probe').value.trim();
    const box = $('probe-result');
    box.textContent = '';
    if (question.length < 3) return;
    try {
      const data = await api('/api/admin/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      if (!data.hits.length) {
        box.textContent = 'Ничего не найдено.';
        return;
      }
      data.hits.forEach((hit) => {
        const item = document.createElement('div');
        item.className = 'source';
        const ref = document.createElement('div');
        ref.className = 'ref';
        ref.textContent = [hit.document, hit.locator, hit.page ? `с. ${hit.page}` : '']
          .filter(Boolean).join(' · ') + ` — ${hit.score}`;
        const excerpt = document.createElement('div');
        excerpt.className = 'excerpt';
        excerpt.textContent = hit.text.slice(0, 300);
        item.appendChild(ref);
        item.appendChild(excerpt);
        box.appendChild(item);
      });
    } catch (err) {
      box.textContent = err.message;
    }
  });

  if (state.token) {
    $('token').value = state.token;
    login();
  }
})();
