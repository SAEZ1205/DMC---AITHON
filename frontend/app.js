document.addEventListener('DOMContentLoaded', () => {
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
    menuToggle.addEventListener('click', () => {
      sidebar.classList.toggle('collapsed');
    });
  }

  const profileButton = document.getElementById('profileButton');
  const profileMenu = document.getElementById('profileMenu');
  if (profileButton && profileMenu) {
    profileButton.addEventListener('click', (event) => {
      event.stopPropagation();
      profileMenu.classList.toggle('open');
    });

    document.addEventListener('click', (event) => {
      if (!profileMenu.contains(event.target) && !profileButton.contains(event.target)) {
        profileMenu.classList.remove('open');
      }
    });
  }

  document.querySelectorAll('.side-item').forEach((item) => {
    item.addEventListener('click', (event) => {
      event.preventDefault();
      document.querySelectorAll('.side-item').forEach((other) => other.classList.remove('active'));
      item.classList.add('active');
    });
  });
});
