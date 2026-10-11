document.addEventListener('DOMContentLoaded', () => {
  // Carga de mejoras visuales sin mezclar la futura lógica de IA.
  if (!document.querySelector('link[href="enhancements.css"]')) {
    const extraCss = document.createElement('link');
    extraCss.rel = 'stylesheet';
    extraCss.href = 'enhancements.css';
    document.head.appendChild(extraCss);
  }

  // Favicon DMC.
  let favicon = document.querySelector('link[rel~="icon"]');
  if (!favicon) {
    favicon = document.createElement('link');
    favicon.rel = 'icon';
    document.head.appendChild(favicon);
  }
  favicon.type = 'image/png';
  favicon.href = 'assets/dmc-logo.png';

  // Logo real DMC en login y cabecera.
  document.querySelectorAll('.dmc-logo').forEach((logo) => {
    logo.classList.add('brand');
    logo.innerHTML = '<img src="assets/dmc-logo.png" alt="DMC"><span class="brand-divider"></span><span class="brand-word">INSTITUTE</span>';
  });

  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
      event.preventDefault();
      window.location.href = 'dashboard.html';
    });
  }

  const menuToggle = document.getElementById('menuToggle');
  const sidebar = document.getElementById('sidebar');
  if (menuToggle && sidebar) {
    menuToggle.addEventListener('click', () => sidebar.classList.toggle('collapsed'));
  }

  // Etiquetas flotantes como en DMC cuando el sidebar está contraído.
  document.querySelectorAll('.side-item').forEach((item) => {
    const label = item.querySelector('span')?.textContent?.trim();
    if (label) item.dataset.label = label;
  });

  // Menús superiores NOSOTROS / PRODUCTOS / EXPERTOS.
  const topNav = document.querySelector('.top-nav');
  if (topNav && !topNav.dataset.enhanced) {
    topNav.dataset.enhanced = 'true';
    topNav.innerHTML = `
      <div class="nav-drop">
        <button class="nav-drop-toggle" type="button">NOSOTROS <i class="fa-solid fa-caret-down"></i></button>
        <div class="nav-dropdown nav-small"><a href="#">INICIO</a></div>
      </div>
      <div class="nav-drop">
        <button class="nav-drop-toggle" type="button">PRODUCTOS <i class="fa-solid fa-caret-down"></i></button>
        <div class="nav-dropdown nav-products">
          <a href="#">Diploma Internacional</a><a href="#">Bootcamps</a><a href="#">Certificación</a>
          <a href="#">Cursos</a><a href="#">Diplomas</a><a href="#">Especializaciones</a>
          <a href="#">Inhouse</a><a href="#">Mentoría</a><a href="#">Master</a>
        </div>
      </div>
      <a href="#">TÉRMINOS Y CONDICIONES</a>
      <a href="#">POLÍTICAS EN CLASES</a>
      <div class="nav-drop">
        <button class="nav-drop-toggle" type="button">EXPERTOS <i class="fa-solid fa-caret-down"></i></button>
        <div class="nav-dropdown nav-small"><a href="#">EXPERTOS DMC</a></div>
      </div>`;
  }

  const profileButton = document.getElementById('profileButton');
  const profileMenu = document.getElementById('profileMenu');
  if (profileButton && profileMenu) {
    profileButton.addEventListener('click', (event) => {
      event.stopPropagation();
      profileMenu.classList.toggle('open');
      document.querySelectorAll('.nav-drop.open').forEach((drop) => drop.classList.remove('open'));
    });
  }

  document.querySelectorAll('.nav-drop-toggle').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      const current = button.closest('.nav-drop');
      document.querySelectorAll('.nav-drop.open').forEach((drop) => {
        if (drop !== current) drop.classList.remove('open');
      });
      current.classList.toggle('open');
      profileMenu?.classList.remove('open');
    });
  });

  document.addEventListener('click', (event) => {
    if (profileMenu && profileButton && !profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
      profileMenu.classList.remove('open');
    }
    if (!event.target.closest('.nav-drop')) {
      document.querySelectorAll('.nav-drop.open').forEach((drop) => drop.classList.remove('open'));
    }
  });

  document.querySelectorAll('.side-item').forEach((item) => {
    item.addEventListener('click', (event) => {
      if (item.getAttribute('href') === '#') {
        event.preventDefault();
        document.querySelectorAll('.side-item').forEach((other) => other.classList.remove('active'));
        item.classList.add('active');
      }
    });
  });

  // Curso demo -> pantalla DMCinos.
  const courseCard = document.querySelector('.course-card');
  if (courseCard && !courseCard.closest('a')) {
    courseCard.classList.add('course-card-link');
    courseCard.tabIndex = 0;
    courseCard.setAttribute('role', 'link');
    const goCourse = () => window.location.href = 'dmcinos.html';
    courseCard.addEventListener('click', goCourse);
    courseCard.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') goCourse();
    });
  }

  // Footer como la referencia enviada.
  const footer = document.querySelector('.site-footer');
  if (footer) {
    footer.innerHTML = `
      <div class="contact-block">
        <p>Mantente en contacto</p>
        <a href="https://dmc.pe" target="_blank" rel="noreferrer"><i class="fa-solid fa-globe"></i> https://dmc.pe</a>
        <a href="tel:+51908866137"><i class="fa-solid fa-phone"></i> +51 908 866 137</a>
        <a href="mailto:customer.experience@dmc.pe"><i class="fa-regular fa-envelope"></i> customer.experience@dmc.pe</a>
      </div>
      <div class="footer-right">
        <div class="socials">
          <a href="#" aria-label="Facebook"><i class="fa-brands fa-facebook-f"></i></a>
          <a href="#" aria-label="LinkedIn"><i class="fa-brands fa-linkedin-in"></i></a>
          <a href="#" aria-label="YouTube"><i class="fa-brands fa-youtube"></i></a>
          <a href="#" aria-label="Instagram"><i class="fa-brands fa-instagram"></i></a>
          <a href="#" aria-label="WhatsApp"><i class="fa-brands fa-whatsapp"></i></a>
        </div>
        <button class="app-download" type="button"><i class="fa-solid fa-mobile-screen-button"></i> Descargar la app para dispositivos móviles</button>
      </div>`;
    if (!footer.nextElementSibling?.classList.contains('rights-bar')) {
      const rights = document.createElement('div');
      rights.className = 'rights-bar';
      rights.textContent = 'TODOS LO DERECHOS RESERVADOS © Learning by EV - DMC';
      footer.insertAdjacentElement('afterend', rights);
    }
  }

  document.querySelectorAll('.continue-btn').forEach((button) => {
    button.addEventListener('click', () => window.location.href = 'dashboard.html');
  });
});
