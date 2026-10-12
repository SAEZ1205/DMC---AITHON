document.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('loginForm');
  if (loginForm) {
    loginForm.addEventListener('submit', (event) => {
      event.preventDefault();
      window.location.href = 'dashboard.html';
    });
  }

  const sidebar = document.getElementById('sidebar');
  const menuToggle = document.getElementById('menuToggle');
  if (sidebar && menuToggle) {
    menuToggle.addEventListener('click', () => {
      sidebar.classList.toggle('collapsed');
      menuToggle.setAttribute('aria-expanded', String(!sidebar.classList.contains('collapsed')));
    });
  }

  const profileButton = document.getElementById('profileButton');
  const profileMenu = document.getElementById('profileMenu');
  const closeTopMenus = () => document.querySelectorAll('.nav-drop.open').forEach((drop) => drop.classList.remove('open'));

  if (profileButton && profileMenu) {
    profileButton.addEventListener('click', (event) => {
      event.stopPropagation();
      closeTopMenus();
      profileMenu.classList.toggle('open');
    });
  }

  document.querySelectorAll('.nav-drop-toggle').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const current = button.closest('.nav-drop');
      document.querySelectorAll('.nav-drop.open').forEach((drop) => {
        if (drop !== current) drop.classList.remove('open');
      });
      current?.classList.toggle('open');
      profileMenu?.classList.remove('open');
    });
  });

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.nav-drop')) closeTopMenus();
    if (profileMenu && !event.target.closest('.profile-wrap')) profileMenu.classList.remove('open');
  });

  const coursesToggle = document.querySelector('[data-courses-toggle]');
  const courseSubmenu = document.getElementById('courseSubmenu');
  if (coursesToggle && courseSubmenu) {
    coursesToggle.addEventListener('click', (event) => {
      event.preventDefault();
      courseSubmenu.classList.toggle('open');
      coursesToggle.classList.toggle('submenu-open');
    });
  }

  document.querySelectorAll('.course-card[data-href]').forEach((card) => {
    const go = () => window.location.href = card.dataset.href;
    card.tabIndex = 0;
    card.setAttribute('role', 'link');
    card.addEventListener('click', go);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        go();
      }
    });
  });

  document.querySelectorAll('[data-go]').forEach((element) => {
    element.addEventListener('click', () => {
      window.location.href = element.dataset.go;
    });
  });

  const accessibility = document.getElementById('accessibilityButton');
  const accessibilityPanel = document.getElementById('accessibilityPanel');
  if (accessibility && accessibilityPanel) {
    accessibility.addEventListener('click', (event) => {
      event.stopPropagation();
      accessibilityPanel.classList.toggle('open');
    });
    document.addEventListener('click', (event) => {
      if (!event.target.closest('.accessibility-wrap')) accessibilityPanel.classList.remove('open');
    });
  }
});
