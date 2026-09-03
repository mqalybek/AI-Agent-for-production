/* Публичный чат: вопрос -> /api/ask -> ответ с источниками. */
(() => {
  const chat = document.getElementById('chat');
  const form = document.getElementById('ask-form');
  const input = document.getElementById('question');
  const sendBtn = document.getElementById('send');
  const docsBox = document.getElementById('docs');

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function addMessage(role, text) {
    const node = el('div', `msg ${role}`, text);
    chat.appendChild(node);
    node.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return node;
  }

  function renderSources(container, sources) {
    if (!sources || !sources.length) return;
    const box = el('div', 'sources');
    box.appendChild(el('h4', null, 'Источники'));
    sources.forEach((s) => {
      const item = el('div', 'source');
      const parts = [s.document];
      if (s.locator) parts.push(s.locator);
      if (s.page) parts.push(`с. ${s.page}`);
      const ref = el('div', 'ref', parts.join(' · '));
      if (typeof s.score === 'number') {
        ref.appendChild(el('span', 'score', `сходство ${s.score.toFixed(2)}`));
      }
      item.appendChild(ref);
      item.appendChild(el('div', 'excerpt', s.excerpt));
      box.appendChild(item);
    });
    container.appendChild(box);
  }

  async function loadDocuments() {
    try {
      const response = await fetch('/api/documents');
      const docs = await response.json();
      docsBox.textContent = '';
      if (!docs.length) {
        docsBox.textContent = 'Документы ещё не загружены — ответить будет не по чему.';
        return;
      }
      docs.forEach((doc) => {
        const pill = el('span', 'pill', doc.title);
        pill.title = `${doc.chunks} фрагментов, загружен ${doc.uploaded_at}`;
        docsBox.appendChild(pill);
        docsBox.appendChild(document.createTextNode(' '));
      });
    } catch (err) {
      docsBox.textContent = 'Не удалось получить список документов.';
    }
  }

  async function ask(question) {
    addMessage('user', question);
    const pending = addMessage('bot', 'Ищу в документах');
    pending.classList.add('dots');
    sendBtn.disabled = true;
    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      const data = await response.json();
      pending.classList.remove('dots');
      if (!response.ok) {
        pending.className = 'msg error';
        pending.textContent = data.detail || 'Ошибка сервера.';
        return;
      }
      pending.textContent = data.answer;
      renderSources(pending, data.sources);
    } catch (err) {
      pending.classList.remove('dots');
      pending.className = 'msg error';
      pending.textContent = `Сеть недоступна: ${err.message}`;
    } finally {
      sendBtn.disabled = false;
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = '';
    ask(question);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      form.requestSubmit();
    }
  });

  loadDocuments();
})();
