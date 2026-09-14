(() => {
  let cleanup = () => {};

  function initialize() {
    cleanup();
    cleanup = () => {};
    const input = document.getElementById('page-search');
    const status = document.getElementById('search-status');
    const main = document.querySelector('main');
    if (!input || !status || !main) return;

    const selector = 'tbody tr, .health-row, .activity-row, .certificate-row, .server-card';
    let timer;
    let previousQuery = '';
    let matches = [];
    let current = -1;

    function search() {
      const query = input.value.trim().toLocaleLowerCase();
      const changed = query !== previousQuery;
      const selected = matches[current];
      matches = [];
      main.querySelectorAll(selector).forEach(item => {
        const match = Boolean(query) && !item.querySelector('.empty') &&
          item.textContent.toLocaleLowerCase().includes(query);
        item.classList.toggle('search-match', match);
        if (!match || changed) item.classList.remove('search-blink');
        if (match) matches.push(item);
      });
      // Live updates keep matches highlighted without restarting their animation.
      if (changed && matches.length) {
        void main.offsetWidth;
        matches.forEach(item => item.classList.add('search-blink'));
      }
      if (changed) current = -1;
      else if (selected && matches.includes(selected)) current = matches.indexOf(selected);
      else current = Math.min(current, matches.length - 1);
      previousQuery = query;
      status.hidden = !query;
      const message = !query ? '' : matches.length
        ? `${matches.length} matching item${matches.length === 1 ? '' : 's'} on this page. Press Enter to jump between matches.`
        : 'No matching items on this page.';
      if (status.textContent !== message) status.textContent = message;
    }

    function onInput() {
      clearTimeout(timer);
      timer = null;
      if (!input.value.trim()) search();
      else timer = setTimeout(() => { timer = null; search(); }, 180);
    }

    function onKeydown(event) {
      if (event.key === 'Escape') {
        input.value = '';
        clearTimeout(timer);
        timer = null;
        search();
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        clearTimeout(timer);
        timer = null;
        search();
        if (!matches.length) return;
        current = (current + 1) % matches.length;
        matches[current].scrollIntoView({block: 'center', inline: 'nearest'});
      }
    }
    input.addEventListener('input', onInput);
    input.addEventListener('search', onInput);
    input.addEventListener('keydown', onKeydown);

    // Traffic rows, proxy tables and monitoring cards are replaced during polling.
    const observer = new MutationObserver(records => {
      if (!timer && input.value.trim() && records.some(record =>
        record.target !== status && !status.contains(record.target))) search();
    });
    observer.observe(main, {childList: true, subtree: true, characterData: true});
    cleanup = () => {
      clearTimeout(timer);
      observer.disconnect();
      input.removeEventListener('input', onInput);
      input.removeEventListener('search', onInput);
      input.removeEventListener('keydown', onKeydown);
      matches.forEach(item => item.classList.remove('search-match', 'search-blink'));
    };
    search();
  }

  initialize();
  document.addEventListener('app:before-render', () => cleanup());
  document.addEventListener('app:navigate', initialize);
})();
