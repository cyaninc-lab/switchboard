// Close a dialog on a click on its backdrop, but only if the press started there too:
// a drag that starts in the dialog (selecting text) and ends outside it must not close it.
export function backdrop(close: () => void) {
  let down = false;
  return {
    onpointerdown: (e: PointerEvent) => { down = e.target === e.currentTarget; },
    onclick: (e: MouseEvent) => { if (down && e.target === e.currentTarget) close(); down = false; },
  };
}
