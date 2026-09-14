const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element extends EventTarget {
  constructor(text = '', empty = false) {
    super();
    this.textContent = text;
    this.empty = empty;
    this.value = '';
    this.children = [];
    this.scrolled = 0;
    this.blinks = 0;
    const classes = new Set();
    this.classList = {
      add: name => { if (name === 'search-blink' && !classes.has(name)) this.blinks++; classes.add(name); },
      remove: (...names) => names.forEach(name => classes.delete(name)),
      contains: name => classes.has(name),
      toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name),
    };
  }
  contains(node) { return node === this || this.children.includes(node); }
  querySelector() { return this.empty ? {} : null; }
  querySelectorAll() { return this.children; }
  scrollIntoView() { this.scrolled++; }
}

function page(...rows) {
  const main = new Element();
  main.children = rows;
  return {main, input: new Element(), status: new Element()};
}

function setup(initial) {
  let active = initial;
  let sequence = 0;
  const timers = new Map();
  const observers = [];
  const document = new EventTarget();
  document.getElementById = id => active?.[id === 'page-search' ? 'input' : 'status'];
  document.querySelector = () => active?.main;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/search.js'), 'utf8'), {
    document,
    setTimeout: callback => { timers.set(++sequence, callback); return sequence; },
    clearTimeout: id => timers.delete(id),
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe(target) { this.target = target; this.connected = true; }
      disconnect() { this.connected = false; }
    },
  });
  return {
    document, timers, observers,
    navigate(next) {
      document.dispatchEvent(new Event('app:before-render'));
      active = next;
      document.dispatchEvent(new Event('app:navigate'));
    },
    flush() {
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach(callback => callback());
    },
    mutate(target) {
      observers.filter(observer => observer.connected).forEach(observer => observer.callback([{target}]));
    },
  };
}

function type(input, text) {
  input.value = text;
  input.dispatchEvent(new Event('input'));
}

function press(input, key) {
  const event = new Event('keydown', {cancelable: true});
  event.key = key;
  input.dispatchEvent(event);
  return event;
}

test('search matches text case-insensitively, excludes empty rows and clears immediately', () => {
  const match = new Element('Example.COM 10.0.0.2');
  const other = new Element('another.test 10.0.0.3');
  const empty = new Element('No example.com records', true);
  const current = page(match, other, empty);
  const app = setup(current);
  type(current.input, '  EXAMPLE.com  ');
  app.flush();
  assert.equal(match.classList.contains('search-match'), true);
  assert.equal(match.blinks, 1);
  assert.equal(other.classList.contains('search-match'), false);
  assert.equal(empty.classList.contains('search-match'), false);
  assert.match(current.status.textContent, /^1 matching item on this page/);
  type(current.input, 'missing');
  app.flush();
  assert.match(current.status.textContent, /^No matching items/);
  assert.equal(match.classList.contains('search-blink'), false);
  type(current.input, '10.0.0.');
  app.flush();
  assert.match(current.status.textContent, /^2 matching items/);
  press(current.input, 'Escape');
  assert.equal(current.input.value, '');
  assert.equal(current.status.hidden, true);
  assert.equal(match.classList.contains('search-match'), false);
  assert.equal(other.classList.contains('search-blink'), false);
  type(current.input, 'example');
  type(current.input, '');
  assert.equal(app.timers.size, 0);
  assert.equal(current.status.textContent, '');
});

test('Enter runs pending search immediately and cycles through matching items', () => {
  const first = new Element('example.com');
  const second = new Element('example.org');
  const current = page(first, second);
  const app = setup(current);
  type(current.input, 'example');
  assert.equal(press(current.input, 'Enter').defaultPrevented, true);
  assert.equal(app.timers.size, 0);
  assert.equal(first.scrolled, 1);
  press(current.input, 'Enter');
  assert.equal(second.scrolled, 1);
  press(current.input, 'Enter');
  assert.equal(first.scrolled, 2);
  assert.equal(first.blinks, 1);
  type(current.input, 'org');
  press(current.input, 'Enter');
  assert.equal(second.scrolled, 2);
  assert.equal(second.blinks, 2);
});

test('live replacements update highlights without repeatedly blinking or resetting selection', () => {
  const first = new Element('example.com');
  const second = new Element('example.org');
  const current = page(first, second);
  const app = setup(current);
  type(current.input, 'example');
  app.flush();
  press(current.input, 'Enter');
  app.mutate(new Element('Updated at 12:00'));
  assert.equal(first.blinks, 1);
  press(current.input, 'Enter');
  assert.equal(second.scrolled, 1);
  const replacement = new Element('example.net');
  current.main.children = [replacement];
  app.mutate(current.main);
  assert.equal(replacement.classList.contains('search-match'), true);
  assert.equal(replacement.blinks, 0);
  assert.match(current.status.textContent, /^1 matching item/);
  press(current.input, 'Enter');
  assert.equal(replacement.scrolled, 1);
  replacement.textContent = 'different.net';
  app.mutate(replacement);
  assert.equal(replacement.classList.contains('search-match'), false);
  assert.match(current.status.textContent, /^No matching items/);
});

test('navigation cancels pending work, detaches old listeners and initializes new pages', () => {
  const original = page(new Element('original.example'));
  const app = setup(original);
  type(original.input, 'original');
  app.navigate(null); // Login has no page-search input.
  assert.equal(app.timers.size, 0);
  assert.equal(app.observers[0].connected, false);
  type(original.input, 'ignored');
  assert.equal(app.timers.size, 0);
  const next = page(new Element('next.example'));
  app.navigate(next);
  type(next.input, 'next');
  app.flush();
  assert.equal(next.main.children[0].classList.contains('search-match'), true);
  assert.equal(app.observers.filter(observer => observer.connected).length, 1);
  app.navigate(next); // Failed navigation restarts the existing page controllers.
  assert.equal(app.observers.filter(observer => observer.connected).length, 1);
  assert.equal(next.main.children[0].classList.contains('search-match'), true);
});
