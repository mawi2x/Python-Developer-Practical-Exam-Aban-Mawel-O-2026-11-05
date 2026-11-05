function setupMoveForms(root = document) {
  root.querySelectorAll('[data-move-form]').forEach((moveForm) => {
    if (moveForm.dataset.initialized) return;
    moveForm.dataset.initialized = 'true';
    const placement = moveForm.elements.placement;
    const target = moveForm.elements.target_id;
    const targetField = moveForm.querySelector('[data-target-field]');
    const updateDestination = () => {
      // Keep a rejected destination visible so its server error can be corrected.
      const showTarget = placement.value === 'before' || target.getAttribute('aria-invalid') === 'true';
      targetField.hidden = !showTarget;
      target.disabled = !showTarget;
      target.required = placement.value === 'before';
    };
    placement.addEventListener('change', () => {
      target.removeAttribute('aria-invalid');
      updateDestination();
    });
    updateDestination();
  });
}
setupMoveForms();

const errorSummary = document.querySelector('[data-error-summary]');
if (errorSummary) errorSummary.focus();

const editModal = document.querySelector('#edit-car-modal');
if (editModal && typeof editModal.showModal === 'function') {
  const modalBody = editModal.querySelector('[data-modal-body]');
  const notice = editModal.querySelector('[data-modal-notice]');
  let loadingRequest;
  let saving = false;
  let opener;

  const focusForm = () => {
    setupMoveForms(modalBody);
    const focusTarget = modalBody.querySelector('[data-error-summary], input[name="name"], select[name="placement"]');
    if (focusTarget) focusTarget.focus();
  };
  const showNotice = (message) => {
    notice.textContent = message;
    notice.hidden = false;
  };

  document.addEventListener('click', async (event) => {
    const link = event.target.closest('a[data-edit-modal], a[data-move-modal]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener = link;
    const title = link.hasAttribute('data-move-modal') ? 'Move car' : 'Edit car';
    editModal.querySelector('#edit-modal-title').textContent = title;
    editModal.querySelector('.modal-close').setAttribute('aria-label', 'Close ' + title.toLowerCase() + ' form');
    loadingRequest?.abort();
    loadingRequest = new AbortController();
    notice.hidden = true;
    modalBody.textContent = 'Loading car details…';
    if (!editModal.open) editModal.showModal();
    document.body.classList.add('modal-open');
    try {
      const response = await fetch(link.href, {
        headers: {'X-Modal-Request': 'true'},
        credentials: 'same-origin', cache: 'no-store', signal: loadingRequest.signal,
      });
      if (!response.ok) throw new Error('load');
      modalBody.innerHTML = await response.text();
      focusForm();
    } catch (error) {
      if (error.name !== 'AbortError') {
        modalBody.textContent = '';
        showNotice('Could not load this car. Close this window and refresh the list.');
      }
    }
  });

  editModal.addEventListener('click', (event) => {
    if (event.target.closest('[data-close-modal]') && !saving) editModal.close();
  });
  editModal.addEventListener('cancel', (event) => {
    if (saving) event.preventDefault();
  });
  editModal.addEventListener('close', () => {
    loadingRequest?.abort();
    document.body.classList.remove('modal-open');
    opener?.focus();
  });

  editModal.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!form.matches('[data-modal-edit-form], [data-modal-move-form]')) return;
    event.preventDefault();
    if (saving) return;
    saving = true;
    notice.hidden = true;
    const saveButton = form.querySelector('button[type="submit"]');
    const originalLabel = saveButton.textContent;
    saveButton.disabled = true;
    saveButton.textContent = 'Saving…';
    editModal.querySelectorAll('[data-close-modal]').forEach(button => button.disabled = true);
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form),
        headers: {'X-Modal-Request': 'true'}, credentials: 'same-origin',
      });
      if (response.status === 422) {
        modalBody.innerHTML = await response.text();
        focusForm();
      } else if (response.ok && response.headers.get('Content-Type')?.includes('application/json')) {
        const result = await response.json();
        window.location.assign(result.redirect_url);
      } else {
        showNotice(response.status === 404
          ? 'This car no longer exists. Close this window and refresh the list.'
          : 'Could not save changes. Refresh the list to check the car before trying again.');
      }
    } catch {
      showNotice('Connection interrupted. Refresh the list to check whether your changes were saved.');
    } finally {
      saving = false;
      saveButton.disabled = false;
      saveButton.textContent = originalLabel;
      editModal.querySelectorAll('[data-close-modal]').forEach(button => button.disabled = false);
    }
  });
}
