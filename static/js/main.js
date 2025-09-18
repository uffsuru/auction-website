// Modal functionality
document.addEventListener('DOMContentLoaded', function() {
    const loginModal = document.getElementById('loginModal');
    const registerModal = document.getElementById('registerModal');
    const loginBtn = document.getElementById('loginBtn');
    const registerBtn = document.getElementById('registerBtn');
    const closeLoginModal = document.getElementById('closeLoginModal');
    const closeRegisterModal = document.getElementById('closeRegisterModal');
    const loginPrompt = document.getElementById('loginPrompt');

    if (loginBtn) loginBtn.onclick = () => loginModal.style.display = 'block';
    if (registerBtn) registerBtn.onclick = () => registerModal.style.display = 'block';
    if (closeLoginModal) closeLoginModal.onclick = () => loginModal.style.display = 'none';
    if (closeRegisterModal) closeRegisterModal.onclick = () => registerModal.style.display = 'none';
    if (loginPrompt) loginPrompt.onclick = (e) => {
        e.preventDefault();
        loginModal.style.display = 'block';
    };

    window.onclick = function(event) {
        if (event.target == loginModal) {
            loginModal.style.display = 'none';
        }
        if (event.target == registerModal) {
            registerModal.style.display = 'none';
        }
    }

    // Mobile Nav Toggle
    const mobileNavToggle = document.getElementById('mobileNavToggle');
    const navLinks = document.getElementById('navLinks');
    if (mobileNavToggle) {
        mobileNavToggle.addEventListener('click', () => {
            navLinks.classList.toggle('active');
        });
    }

    // Login form handling
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const email = document.getElementById('loginEmail').value;
            const password = document.getElementById('loginPassword').value;
            const errorDiv = document.getElementById('loginError');
            errorDiv.style.display = 'none';
            
            try {
                const response = await fetch(appConfig.loginUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const result = await response.json();
                
                if (result.success) {
                    window.location.reload();
                } else {
                    errorDiv.textContent = result.message;
                    errorDiv.style.display = 'block';
                }
            } catch (error) {
                console.error('Login error:', error);
                errorDiv.textContent = 'An error occurred. Please try again.';
                errorDiv.style.display = 'block';
            }
        });
    }

    // Register form handling
    const registerForm = document.getElementById('registerForm');
    if (registerForm) {
        registerForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const name = document.getElementById('registerName').value;
            const email = document.getElementById('registerEmail').value;
            const password = document.getElementById('registerPassword').value;
            const errorDiv = document.getElementById('registerError');
            errorDiv.style.display = 'none';
            
            try {
                const response = await fetch(appConfig.registerUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, email, password })
                });
                const result = await response.json();
                
                if (result.success) {
                    registerModal.style.display = 'none';
                    loginModal.style.display = 'block';
                    document.getElementById('loginEmail').value = email;
                    const loginErrorDiv = document.getElementById('loginError');
                    loginErrorDiv.textContent = 'Registration successful! Please log in.';
                    loginErrorDiv.style.color = 'green';
                    loginErrorDiv.style.display = 'block';
                } else {
                    errorDiv.textContent = result.message;
                    errorDiv.style.display = 'block';
                }
            } catch (error) {
                console.error('Registration error:', error);
                errorDiv.textContent = 'An error occurred. Please try again.';
                errorDiv.style.display = 'block';
            }
        });
    }

    // Notifications
    const notificationBell = document.querySelector('.notification-bell');
    const notificationCount = document.getElementById('notification-count');
    const notificationsDropdown = document.getElementById('notifications-dropdown');

    if (notificationBell) {
        notificationBell.addEventListener('click', () => {
            const isVisible = notificationsDropdown.style.display === 'block';
            notificationsDropdown.style.display = isVisible ? 'none' : 'block';
            
            if (!isVisible && notificationCount && parseInt(notificationCount.textContent) > 0) {
                fetch(appConfig.markReadUrl, { method: 'POST' })
                    .then(response => {
                        if (response.ok) {
                            notificationCount.style.display = 'none';
                            notificationCount.textContent = '0';
                            document.querySelectorAll('.notification-item.unread').forEach(item => {
                                item.classList.remove('unread');
                            });
                        }
                    });
            }
        });

        // Close dropdown if clicking outside
        document.addEventListener('click', function(event) {
            if (notificationBell && !notificationBell.contains(event.target) && notificationsDropdown && !notificationsDropdown.contains(event.target)) {
                notificationsDropdown.style.display = 'none';
            }
        });
    }

    // Smooth scrolling
    document.querySelectorAll('a[href^="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            e.preventDefault();
            const targetId = this.getAttribute('href');
            if (targetId.length > 1) { // Make sure it's not just "#"
                const target = document.querySelector(targetId);
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth'
                    });
                }
            }
        });
    });

    // Fetch and render notifications for logged-in users asynchronously
    function loadNotifications() {
        const notificationBell = document.querySelector('.notification-bell');
        if (!notificationBell) {
            return; // User is not logged in
        }

        fetch(appConfig.summaryUrl)
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    const countElement = document.getElementById('notification-count');
                    const dropdownElement = document.getElementById('notifications-dropdown');
                    
                    // Update count
                    if (data.unread_count > 0) {
                        countElement.textContent = data.unread_count;
                        countElement.style.display = 'block';
                    } else {
                        countElement.style.display = 'none';
                    }

                    // Populate dropdown
                    dropdownElement.innerHTML = ''; // Clear loading message
                    if (data.notifications.length > 0) {
                        data.notifications.forEach(n => {
                            const item = document.createElement('a');
                            item.href = n.link;
                            item.className = 'notification-item';
                            if (!n.is_read) {
                                item.classList.add('unread');
                            }
                            item.textContent = n.message;
                            dropdownElement.appendChild(item);
                        });
                    } else {
                        dropdownElement.innerHTML = '<div style="padding: 12px 16px; color: #666;">No notifications yet.</div>';
                    }
                }
            })
            .catch(error => console.error('Error fetching notifications:', error));
    }

    loadNotifications();
});