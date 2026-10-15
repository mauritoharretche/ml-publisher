document.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector('input[name="images"]');
  const container = document.querySelector("#previews");
  if (!input || !container) return;
  input.addEventListener("change", () => {
    container.replaceChildren();
    [...input.files].forEach(file => {
      if (!file.type.startsWith("image/")) return;
      const image = document.createElement("img");
      image.src = URL.createObjectURL(file); image.alt = file.name;
      container.append(image);
    });
  });
});
