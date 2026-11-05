const moveForm = document.querySelector('[data-move-form]');
if (moveForm) {
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
}

const errorSummary = document.querySelector('[data-error-summary]');
if (errorSummary) errorSummary.focus();
