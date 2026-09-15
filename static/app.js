/* Публичный чат: диалог с памятью, источники и оценка ответов. */
(() => {
  const chat = document.getElementById('chat');
  const form = document.getElementById('ask-form');
  const input = document.getElementById('question');
  const sendBtn = document.getElementById('send');
  const docsBox = document.getElementById('docs');
  const resetBtn = document.getElementById('reset');

  const KEY = 'subsoil_conversation_id';
  const state = { conversationId: sessionStorage.getItem(KEY) || '', busy: false };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function scrollToEnd(node) {
    node.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  function addMessage(role, text) {
    const node = el('div', `msg ${role}`, text);
    chat.appendChild(node);
    scrollToEnd(node);
    return node;
  }

  function renderSources(container, sources) {
    if (!sources || !sources.length) return;
    const box = el('details', 'sources');
    const summary = el('summary', null, `Источники (${sources.length})`);
    box.appendChild(summary);
    sources.forEach((s) => {
      const item = el('div', 'source');
      const parts = [s.document];
      if (s.locator) parts.push(s.locator);
      if (s.chapter) parts.push(s.chapter);
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

  /* Оценка ответа. Палец вниз открывает поле «что именно не так» —
     этот текст видит администратор в панели. */
  function renderFeedback(container, messageId) {
    if (!messageId) return;
    const bar = el('div', 'feedback');
    const status = el('span', 'feedback-status');

    const send = async (rating, comment, button) => {
      try {
        const response = await fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: messageId, rating, comment: comment || '' }),
        });
        if (!response.ok) throw new Error((await response.json()).detail || 'ошибка');
        bar.querySelectorAll('button.vote').forEach((b) => b.classList.remove('chosen'));
        if (button) button.classList.add('chosen');
        status.textContent = rating === 'up' ? 'Спасибо за оценку.' : 'Учтём, спасибо.';
      } catch (err) {
        status.textContent = `Не удалось сохранить оценку: ${err.message}`;
      }
    };

    const up = el('button', 'vote ghost', '👍 Полезно');
    up.addEventListener('click', () => send('up', '', up));

    const down = el('button', 'vote ghost', '👎 Неверно');
    const commentBox = el('div', 'feedback-comment hidden');
    const comment = el('textarea');
    comment.placeholder = 'Что именно не так? Например: «норма устарела» или «не та статья»';
    comment.rows = 2;
    const submit = el('button', null, 'Отправить замечание');
    submit.addEventListener('click', () => {
      send('down', comment.value.trim(), down);
      commentBox.classList.add('hidden');
    });
    commentBox.appendChild(comment);
    commentBox.appendChild(submit);
    down.addEventListener('click', () => {
      commentBox.classList.toggle('hidden');
      if (!commentBox.classList.contains('hidden')) comment.focus();
    });

    bar.appendChild(up);
    bar.appendChild(down);
    bar.appendChild(status);
    container.appendChild(bar);
    container.appendChild(commentBox);
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

  /* Диалог переживает перезагрузку страницы: id лежит в sessionStorage,
     а сами реплики — на сервере. */
  async function restoreConversation() {
    if (!state.conversationId) return;
    try {
      const response = await fetch(`/api/conversations/${state.conversationId}`);
      if (!response.ok) {
        sessionStorage.removeItem(KEY);
        state.conversationId = '';
        return;
      }
      const data = await response.json();
      data.messages.forEach((message) => {
        const node = addMessage(message.role === 'user' ? 'user' : 'bot', message.content);
        if (message.role === 'assistant') {
          renderSources(node, message.sources);
          renderFeedback(node, message.id);
        }
      });
    } catch (err) {
      /* молча начинаем новый диалог */
    }
  }

  async function ask(question) {
    addMessage('user', question);
    const pending = addMessage('bot', 'Ищу в документах');
    pending.classList.add('dots');
    state.busy = true;
    sendBtn.disabled = true;
    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          conversation_id: state.conversationId || null,
        }),
      });
      const data = await response.json();
      pending.classList.remove('dots');
      if (!response.ok) {
        pending.className = 'msg error';
        pending.textContent = data.detail || 'Ошибка сервера.';
        return;
      }
      state.conversationId = data.conversation_id;
      sessionStorage.setItem(KEY, data.conversation_id);
      pending.textContent = data.answer;
      renderSources(pending, data.sources);
      renderFeedback(pending, data.message_id);
      scrollToEnd(pending);
    } catch (err) {
      pending.classList.remove('dots');
      pending.className = 'msg error';
      pending.textContent = `Сеть недоступна: ${err.message}`;
    } finally {
      state.busy = false;
      sendBtn.disabled = false;
      input.focus();
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question || state.busy) return;
    input.value = '';
    ask(question);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      form.requestSubmit();
    }
  });

  resetBtn.addEventListener('click', () => {
    sessionStorage.removeItem(KEY);
    state.conversationId = '';
    chat.textContent = '';
    input.focus();
  });

  loadDocuments();
  restoreConversation();
})();
