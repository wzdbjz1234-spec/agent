// DataHarness 教程测验组件：零依赖、原生 JS。
// 用法：每个 .quiz 块内放 <h4>题干</h4>、若干 <button class="option" data-correct="true|false">、一个 <div class="feedback">、
// 可选 <details><summary>查看解析</summary>...</details>。
// 页面底部引入 <script src="assets/quiz.js" defer></script> 即可。
(function () {
  "use strict";

  function wireQuiz(quiz) {
    var buttons = quiz.querySelectorAll("button.option");
    var feedback = quiz.querySelector(".feedback");
    var done = false;

    function show(result, message) {
      if (!feedback) return;
      feedback.className = "feedback " + result;
      feedback.textContent = message;
    }

    Array.prototype.forEach.call(buttons, function (btn) {
      btn.addEventListener("click", function () {
        if (done) return;
        var correct = btn.getAttribute("data-correct") === "true";
        if (correct) {
          done = true;
          btn.classList.add("correct");
          show("correct", "正确！");
        } else {
          btn.classList.add("wrong");
          show("wrong", "不对，再想想。可展开下方解析。");
        }
      });
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".quiz"), wireQuiz);
})();
