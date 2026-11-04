(function () {
  var form = document.getElementById('scanForm');
  var field = document.getElementById('scanToken');
  var video = document.getElementById('scanVideo');
  var start = document.getElementById('scanStart');
  var file = document.getElementById('scanFile');
  var note = document.getElementById('scanNote');
  var stream = null;
  var busy = false;

  function say(text) { if (note) { note.textContent = text; } }

  function submit(text) {
    if (busy || !text) { return; }
    busy = true;
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); }
    field.value = text;
    form.submit();
  }

  if (!('BarcodeDetector' in window)) {
    say('This browser cannot read QR codes by itself. Paste the code below instead, or open this page in Chrome or Edge.');
    if (start) { start.disabled = true; }
    if (file) { file.disabled = true; }
    return;
  }

  var detector = new BarcodeDetector({ formats: ['qr_code'] });

  function look() {
    if (busy || !stream) { return; }
    detector.detect(video).then(function (codes) {
      if (codes.length) { submit(codes[0].rawValue); } else { setTimeout(look, 250); }
    }).catch(function () { setTimeout(look, 400); });
  }

  if (start) {
    start.addEventListener('click', function () {
      navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }).then(function (s) {
        stream = s;
        video.srcObject = s;
        video.hidden = false;
        video.play();
        say('Point the camera at the teacher\'s QR code.');
        look();
      }).catch(function () { say('The camera could not be opened. Allow camera access or paste the code below.'); });
    });
  }

  if (file) {
    file.addEventListener('change', function () {
      var chosen = file.files && file.files[0];
      if (!chosen) { return; }
      createImageBitmap(chosen).then(function (bitmap) { return detector.detect(bitmap); }).then(function (codes) {
        if (codes.length) { submit(codes[0].rawValue); } else { say('No QR code was found in that image. Try a sharper photo or paste the code.'); }
      }).catch(function () { say('That image could not be read. Paste the code instead.'); });
    });
  }
})();
