(() => {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const palettes = {
    "teal-grid": { background: "#062f34", grid: "#15575c", primary: "#58e0c3", secondary: "#ffcf70" },
    "indigo-lab": { background: "#151d43", grid: "#34437d", primary: "#8ba5ff", secondary: "#ff9ab2" },
  };

  const drawFrame = (context, palette, time, variant) => {
    const { width, height } = context.canvas;
    context.fillStyle = palette.background;
    context.fillRect(0, 0, width, height);
    context.strokeStyle = palette.grid;
    context.lineWidth = 1;
    for (let x = 0; x <= width; x += 40) {
      context.beginPath(); context.moveTo(x, 0); context.lineTo(x, height); context.stroke();
    }
    for (let y = 0; y <= height; y += 40) {
      context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
    }

    context.fillStyle = "rgba(255,255,255,.08)";
    for (let row = 0; row < 3; row += 1) {
      for (let column = 0; column < 5; column += 1) {
        context.fillRect(70 + column * 105, 80 + row * 78, 66, 42);
      }
    }
    const speed = variant === "indigo-lab" ? 0.00012 : 0.00009;
    const x = 50 + ((time * speed * width) % (width - 100));
    const y = variant === "indigo-lab" ? 218 + Math.sin(time / 580) * 28 : 132 + Math.sin(time / 720) * 34;
    context.fillStyle = palette.primary;
    context.beginPath(); context.arc(x, y, 22, 0, Math.PI * 2); context.fill();
    context.fillStyle = palette.secondary;
    context.fillRect(width - x - 28, height - y + 34, 30, 30);

    context.fillStyle = "rgba(255,255,255,.88)";
    context.font = "600 18px system-ui, sans-serif";
    context.fillText("SYNTHETIC DEMO · NO REAL CAMERA", 22, 32);
    context.font = "14px system-ui, sans-serif";
    context.fillText(new Date().toLocaleTimeString("ko-KR"), 22, height - 20);
  };

  document.querySelectorAll("[data-synthetic-video]").forEach((video) => {
    const error = video.parentElement?.querySelector("[data-video-error]");
    try {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 360;
      const context = canvas.getContext("2d");
      const variant = video.dataset.variant || "teal-grid";
      const palette = palettes[variant] || palettes["teal-grid"];
      if (!context || typeof canvas.captureStream !== "function") throw new Error("unsupported");

      let animationId = 0;
      const render = (time) => {
        drawFrame(context, palette, time, variant);
        if (!reducedMotion) animationId = window.requestAnimationFrame(render);
      };
      render(0);
      video.srcObject = canvas.captureStream(reducedMotion ? 1 : 12);
      video.muted = true;
      video.playsInline = true;
      if (!reducedMotion) video.play().catch(() => undefined);
      video.addEventListener("emptied", () => window.cancelAnimationFrame(animationId), { once: true });
    } catch (_) {
      video.hidden = true;
      if (error) error.hidden = false;
    }
  });

  const clock = document.querySelector("[data-current-time]");
  if (clock) {
    const updateClock = () => {
      clock.textContent = new Intl.DateTimeFormat("ko-KR", {
        dateStyle: "medium", timeStyle: "medium", timeZone: "Asia/Seoul",
      }).format(new Date());
    };
    updateClock();
    window.setInterval(updateClock, 1000);
  }
})();
