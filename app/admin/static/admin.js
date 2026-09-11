(() => {
  "use strict";

  const announce = (message) => {
    let live = document.querySelector("[data-copy-status]");
    if (!live) {
      live = document.createElement("span");
      live.className = "sr-only";
      live.setAttribute("data-copy-status", "true");
      live.setAttribute("aria-live", "polite");
      document.body.appendChild(live);
    }
    live.textContent = message;
  };

  document.addEventListener("click", async (event) => {
    const target = event.target;
    const button = target instanceof Element ? target.closest("[data-copy]") : null;
    if (!(button instanceof HTMLElement)) return;
    const value = button.getAttribute("data-copy");
    if (!value || !navigator.clipboard) {
      announce("이 브라우저에서는 복사를 사용할 수 없습니다.");
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      const original = button.textContent;
      button.textContent = "복사됨";
      button.setAttribute("aria-label", "복사 완료");
      announce("식별자를 클립보드에 복사했습니다.");
      window.setTimeout(() => {
        button.textContent = original;
        button.setAttribute("aria-label", "식별자 복사");
      }, 1200);
    } catch {
      announce("식별자를 복사하지 못했습니다.");
    }
  });
})();
