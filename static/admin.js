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

  async function loadTopics() {
    const box = $('topics');
    box.textContent = '';
    const { topics } = await api('/api/admin/topics');
    topics.forEach((topic) => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;gap:8px;align-items:center;margin:4px 0';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = topic.name;
      input.name = 'topic';
      input.style.width = 'auto';
      // Уран исключается по умолчанию: ассистент предметный, по углеводородам.
      input.checked = topic.name !== 'underground_space';
      label.appendChild(input);
      label.appendChild(document.createTextNode(topic.label));
      box.appendChild(label);
    });
  }

  function selectedTopics() {
    return Array.from(document.querySelectorAll('#topics input[name=topic]:checked'))
      .map((input) => input.value)
      .join(',');
  }

  async function loadFeedback(rating) {
    const body = $('feedback-body');
    body.textContent = '';
    const query = rating ? `?rating=${rating}` : '';
    const items = await api(`/api/admin/feedback${query}`);
    if (!items.length) {
      setStatus($('feedback-status'), 'Оценок пока нет.', null);
      return;
    }
    setStatus($('feedback-status'), '', null);
    items.forEach((item) => {
      const tr = document.createElement('tr');
      tr.className = 'feedback-row';

      const mark = document.createElement('td');
      mark.className = item.rating === 'up' ? 'rating-up' : 'rating-down';
      mark.textContent = item.rating === 'up' ? '👍 полезно' : '👎 неверно';

      const text = document.createElement('td');
      text.appendChild(document.createTextNode(item.question || '(вопрос не сохранён)'));
      if (item.comment) {
        const comment = document.createElement('div');
        comment.className = 'quote';
        comment.textContent = `Замечание: ${item.comment}`;
        text.appendChild(comment);
      }
      const answer = document.createElement('div');
      answer.className = 'quote';
      answer.textContent = `Ответ: ${item.answer.slice(0, 200)}${item.answer.length > 200 ? '…' : ''}`;
      text.appendChild(answer);

      const when = document.createElement('td');
      when.textContent = item.created_at.replace('T', ' ').replace('+00:00', ' UTC');

      [mark, text, when].forEach((cell) => tr.appendChild(cell));
      body.appendChild(tr);
    });
  }

  /* Проверка ключа Anthropic: стоит доли цента и сразу отвечает на вопрос
     «почему ассистент не отвечает» — ключа нет, ключ неверный или нет средств. */
  async function checkModel() {
    const box = $('model-status');
    box.textContent = 'Проверяю…';
    box.className = 'status';
    try {
      const data = await api('/api/admin/model-status');
      box.textContent = data.message;
      box.className = data.status === 'ok' ? 'status ok' : 'status err';
    } catch (err) {
      box.textContent = err.message;
      box.className = 'status err';
    }
  }

  const money = (usd) => {
    if (usd === null || usd === undefined) return '—';
    return usd >= 1 ? `$${usd.toFixed(2)}` : `${(usd * 100).toFixed(1)} цента`;
  };

  const thousands = (n) => n.toLocaleString('ru-RU');

  async function refresh() {
    const stats = await api('/api/admin/stats');
    $('stats').textContent =
      `Документов: ${stats.documents} · фрагментов: ${stats.chunks} · ` +
      `эмбеддинги: ${stats.embeddings_provider} · модель: ${stats.model} · ` +
      `диалогов: ${stats.conversations} · оценок: 👍 ${stats.liked} / 👎 ${stats.disliked}`;

    /* Пароль из шаблона знает любой, кто видел репозиторий. */
    const warning = $('token-warning');
    warning.classList.toggle('hidden', !stats.admin_token_is_default);

    /* Фактический расход: считается по ответам модели, а не по оценкам «на глаз». */
    const usage = $('usage');
    usage.textContent = '';
    if (!stats.answers) {
      usage.textContent = 'Модель пока не отвечала — расхода нет.';
    } else {
      const head = document.createElement('div');
      head.textContent =
        `Ответов: ${stats.answers} · токенов: ${thousands(stats.input_tokens)} вход / ` +
        `${thousands(stats.output_tokens)} выход · потрачено ≈ ${money(stats.cost_usd)}`;
      usage.appendChild(head);

      const perAnswer = stats.cost_usd === null ? null : stats.cost_usd / stats.answers;
      const avg = document.createElement('div');
      avg.className = 'muted';
      avg.textContent = `В среднем ${money(perAnswer)} за ответ`;
      usage.appendChild(avg);

      stats.by_model.forEach((row) => {
        const line = document.createElement('div');
        line.className = 'muted';
        line.textContent =
          `${row.model}: ${row.answers} отв. · ${thousands(row.input_tokens)} / ` +
          `${thousands(row.output_tokens)} токенов · ${money(row.cost_usd)}`;
        usage.appendChild(line);
      });
    }

    await loadFeedback();

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
      if (doc.dropped_sections && doc.dropped_sections.length) {
        const dropped = document.createElement('div');
        dropped.className = 'muted';
        dropped.textContent = `исключено разделов: ${doc.dropped_sections.length}`;
        dropped.title = doc.dropped_sections.join('\n');
        chunks.appendChild(dropped);
      }
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
      await loadTopics();
      await checkModel();
      sessionStorage.setItem(KEY, state.token);
      setStatus($('auth-status'), 'Доступ разрешён.', 'ok');
      showPanel(true);
    } catch (err) {
      showPanel(false);
      setStatus($('auth-status'), err.message, 'err');
    }
  }

  $('check-model').addEventListener('click', checkModel);
  $('fb-all').addEventListener('click', () => loadFeedback());
  $('fb-down').addEventListener('click', () => loadFeedback('down'));

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
    form.append('exclude_topics', selectedTopics());

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
      await loadTopics();
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
