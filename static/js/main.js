// SAT+ Platform — Main JavaScript

// ── Navbar ────────────────────────────────────────────────
function toggleUserMenu() {
  document.getElementById('userDropdown')?.classList.toggle('open');
}
function toggleMobileNav() {
  document.getElementById('navLinks')?.classList.toggle('open');
}
document.addEventListener('click', function(e) {
  const dropdown = document.getElementById('userDropdown');
  if (dropdown && !e.target.closest('.nav-user')) {
    dropdown.classList.remove('open');
  }
});

// ── Auto-dismiss alerts ────────────────────────────────────
document.querySelectorAll('.alert').forEach(alert => {
  setTimeout(() => {
    alert.style.opacity = '0';
    alert.style.transform = 'translateX(100%)';
    alert.style.transition = '0.3s ease';
    setTimeout(() => alert.remove(), 300);
  }, 4000);
});

// ── CSRF token helper ─────────────────────────────────────
function getCookie(name) {
  let cookieValue = null;
  if (document.cookie && document.cookie !== '') {
    for (const cookie of document.cookie.split(';')) {
      const c = cookie.trim();
      if (c.startsWith(name + '=')) {
        cookieValue = decodeURIComponent(c.slice(name.length + 1));
        break;
      }
    }
  }
  return cookieValue;
}
const CSRF_TOKEN = getCookie('csrftoken');

// ── Fetch helper ──────────────────────────────────────────
async function post(url, data) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
    body: JSON.stringify(data)
  });
  return res.json();
}
