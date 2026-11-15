// jsdom has no dialog top layer. Exercise the confirmation state here;
// native modal focus/overlay behavior is also checked in Safari.
Object.defineProperties(HTMLDialogElement.prototype, {
  showModal: {
    configurable: true,
    writable: true,
    value(this: HTMLDialogElement) {
      this.open = true
    },
  },
  close: {
    configurable: true,
    writable: true,
    value(this: HTMLDialogElement) {
      this.open = false
    },
  },
})
