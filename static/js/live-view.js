// Commit background updates without losing the reader's place or table scroll.
(() => {
  const containers = '.table-wrap, .usage-table, .sidebar nav';
  window.updateLiveView = update => {
    const left = window.scrollX, top = window.scrollY;
    const positions = [...document.querySelectorAll(containers)].map(node => ({
      left: node.scrollLeft, top: node.scrollTop,
    }));
    update();
    document.querySelectorAll(containers).forEach((node, index) => {
      const saved = positions[index];
      if (saved) { node.scrollLeft = saved.left; node.scrollTop = saved.top; }
    });
    if (window.scrollX !== left || window.scrollY !== top) window.scrollTo(left, top);
  };
})();
