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
  const pageColumn = document.querySelector('.page-column');

  const syncSidebarLayout = () => {
    if (!sidebar || !pageColumn) return;

    const expanded = !sidebar.classList.contains('collapsed');
    pageColumn.style.transition = 'margin-left .25s ease, width .25s ease';

    if (window.innerWidth <= 650) {
      pageColumn.style.setProperty('margin-left', '66px', 'important');
      pageColumn.style.setProperty('width', 'calc(100% - 66px)', 'important');
      return;
    }

    pageColumn.style.setProperty(
      'margin-left',
      expanded ? '361px' : '78px',
      'important'
    );
    pageColumn.style.setProperty(
      'width',
      expanded ? 'calc(100% - 361px)' : 'calc(100% - 78px)',
      'important'
    );
  };

  if (sidebar && menuToggle) {
    const setSidebarState = (expanded) => {
      sidebar.classList.toggle('collapsed', !expanded);
      menuToggle.setAttribute('aria-expanded', String(expanded));

      if (!expanded) {
        document.getElementById('courseSubmenu')?.classList.remove('open');
        document.querySelector('[data-courses-toggle]')?.classList.remove('submenu-open');
      }

      syncSidebarLayout();
    };

    syncSidebarLayout();
    window.addEventListener('resize', syncSidebarLayout);

    menuToggle.addEventListener('click', () => {
      setSidebarState(sidebar.classList.contains('collapsed'));
    });
  }

  const profileButton = document.getElementById('profileButton');
  const profileMenu = document.getElementById('profileMenu');
  const topDrops = [...document.querySelectorAll('.nav-drop')];
  const closeTopMenus = () => topDrops.forEach((drop) => drop.classList.remove('open'));
  const closeProfile = () => profileMenu?.classList.remove('open');

  topDrops.forEach((drop) => {
    const button = drop.querySelector('.nav-drop-toggle');
    if (!button) return;

    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const shouldOpen = !drop.classList.contains('open');
      closeTopMenus();
      closeProfile();
      if (shouldOpen) drop.classList.add('open');
    });

    if (window.matchMedia('(hover:hover) and (pointer:fine)').matches) {
      drop.addEventListener('mouseenter', () => {
        closeTopMenus();
        closeProfile();
        drop.classList.add('open');
      });
      drop.addEventListener('mouseleave', () => drop.classList.remove('open'));
    }
  });

  if (profileButton && profileMenu) {
    profileButton.addEventListener('click', (event) => {
      event.stopPropagation();
      closeTopMenus();
      profileMenu.classList.toggle('open');
    });
  }

  document.addEventListener('click', (event) => {
    if (!event.target.closest('.nav-drop')) closeTopMenus();
    if (!event.target.closest('.profile-wrap')) closeProfile();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      closeTopMenus();
      closeProfile();
      document.getElementById('accessibilityPanel')?.classList.remove('open');
    }
  });

  const coursesToggle = document.querySelector('[data-courses-toggle]');
  const courseSubmenu = document.getElementById('courseSubmenu');
  if (coursesToggle && courseSubmenu) {
    coursesToggle.addEventListener('click', (event) => {
      event.preventDefault();

      if (sidebar?.classList.contains('collapsed')) {
        sidebar.classList.remove('collapsed');
        menuToggle?.setAttribute('aria-expanded', 'true');
        syncSidebarLayout();
      }

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
    element.addEventListener('click', () => window.location.href = element.dataset.go);
  });

  const accessibility = document.getElementById('accessibilityButton');
  const accessibilityPanel = document.getElementById('accessibilityPanel');
  if (accessibility && accessibilityPanel) {
    accessibility.addEventListener('click', (event) => {
      event.stopPropagation();
      accessibilityPanel.classList.toggle('open');
    });

    const buttons = accessibilityPanel.querySelectorAll('button');
    if (buttons[0]) buttons[0].addEventListener('click', () => document.documentElement.style.fontSize = '90%');
    if (buttons[1]) buttons[1].addEventListener('click', () => document.documentElement.style.fontSize = '100%');
    if (buttons[2]) buttons[2].addEventListener('click', () => document.documentElement.style.fontSize = '110%');
  }
});
