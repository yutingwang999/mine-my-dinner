import { useEffect } from "react";

/** The zone near the top or bottom of the window that scrolls the page, in pixels. */
const EDGE = 120;
/** The fastest scroll, in pixels per frame, reached right at the window's edge. */
const MAX_STEP = 24;

/**
 * Scrolls the window while a drag hovers near its top or bottom edge. The browser
 * ignores the mouse wheel during an HTML5 drag and cannot be relied on to scroll
 * the page itself, so without this, lines far apart could never be dropped on
 * each other.
 */
export function useDragAutoScroll(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let pointerY: number | null = null;
    let frame: number | null = null;

    function step() {
      frame = null;
      if (pointerY === null) return;
      const fromTop = pointerY;
      const fromBottom = window.innerHeight - pointerY;
      let speed = 0;
      if (fromTop < EDGE) {
        speed = -Math.ceil(((EDGE - fromTop) / EDGE) * MAX_STEP);
      } else if (fromBottom < EDGE) {
        speed = Math.ceil(((EDGE - fromBottom) / EDGE) * MAX_STEP);
      }
      if (speed !== 0) {
        window.scrollBy(0, speed);
        frame = requestAnimationFrame(step);
      }
    }

    // dragover keeps firing during a drag even while the mouse rests, so holding
    // the pointer at the edge keeps the loop alive until the pointer moves away.
    function onDragOver(event: DragEvent) {
      pointerY = event.clientY;
      if (frame === null) frame = requestAnimationFrame(step);
    }

    window.addEventListener("dragover", onDragOver);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [active]);
}
