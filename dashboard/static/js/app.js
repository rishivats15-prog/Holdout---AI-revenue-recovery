/* The only JavaScript in the project. Three jobs, all optional: the
   scoreboard count-up, the flow diagram's draw-on-mount, and the kill
   switch's typed confirmation. Every page renders correctly and every
   control works with this file blocked — the kill switch's confirmation
   is validated on the server too, so this only makes it faster to notice
   a typo. */

(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* --- count-up ------------------------------------------------------
     Intermediate frames are formatted by the browser's own en-IN
     grouping; the final frame restores the exact string the server
     rendered, so the resting value is always Python's, never a
     re-implementation of the rupee formatter. */
  function countUp(el) {
    var target = parseFloat(el.getAttribute("data-countup"));
    var settled = el.textContent;
    if (isNaN(target) || target === 0) return;

    var prefix = el.getAttribute("data-countup-prefix") || "";
    var format = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
    var duration = 600;
    var started = null;

    function frame(now) {
      if (started === null) started = now;
      var t = Math.min((now - started) / duration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      if (t < 1) {
        el.textContent = prefix + format.format(Math.round(target * eased));
        window.requestAnimationFrame(frame);
      } else {
        el.textContent = settled;
      }
    }
    window.requestAnimationFrame(frame);
  }

  /* --- flow diagram draw --------------------------------------------
     The SVG is fully visible in the markup. Adding the class opts into
     the reveal, so a failure to run this leaves the diagram complete
     rather than blank. */
  function revealFlow(stage) {
    stage.classList.add("is-animated");
  }

  /* --- kill switch ---------------------------------------------------
     Enables the submit button only once HALT is typed exactly. The
     server rejects the post without it regardless. */
  function wireKillSwitch(form) {
    var input = form.querySelector("[data-halt-input]");
    var submit = form.querySelector("[data-halt-submit]");
    if (!input || !submit) return;

    submit.disabled = true;
    input.addEventListener("input", function () {
      submit.disabled = input.value.trim().toUpperCase() !== "HALT";
    });
  }

  /* --- disclosure ----------------------------------------------------
     Reveals the confirmation form. Without JS the <details> fallback in
     the markup already exposes it. */
  function wireDisclosure(button) {
    var target = document.getElementById(button.getAttribute("data-reveals"));
    if (!target) return;
    target.hidden = true;
    button.hidden = false;
    button.addEventListener("click", function () {
      target.hidden = !target.hidden;
      button.setAttribute("aria-expanded", String(!target.hidden));
      if (!target.hidden) {
        var field = target.querySelector("input");
        if (field) field.focus();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-halt-form]"), wireKillSwitch);
    Array.prototype.forEach.call(document.querySelectorAll("[data-reveals]"), wireDisclosure);
    if (reduceMotion) return;
    Array.prototype.forEach.call(document.querySelectorAll("[data-countup]"), countUp);
    Array.prototype.forEach.call(document.querySelectorAll(".flow__stage"), revealFlow);
  });
})();
