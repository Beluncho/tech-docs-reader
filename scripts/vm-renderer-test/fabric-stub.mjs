// Заглушка fabric для теста логики рендерера вне браузера
export class FabricObject {
  constructor(opts = {}) { Object.assign(this, opts); }
}
export class Rect extends FabricObject {}
export class Textbox extends FabricObject {
  constructor(text, opts = {}) { super({ text, ...opts }); }
}
export class Canvas {
  constructor(el, opts = {}) { this.el = el; this.opts = opts; this.objects = []; this.handlers = {}; this.dims = null; this.vpt = null; }
  add(...objs) { this.objects.push(...objs); return this; }
  remove(...objs) { this.objects = this.objects.filter(o => !objs.includes(o)); return this; }
  clear() { this.objects = []; }
  setDimensions(d) { this.dims = d; }
  setViewportTransform(v) { this.vpt = v; }
  requestRenderAll() {}
  dispose() {}
  on(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); }
  fire(ev, opt) { (this.handlers[ev] || []).forEach(fn => fn(opt)); }
}
