document.addEventListener('DOMContentLoaded', function() {
    // --- Tab switching logic ---
    const tabs = document.querySelectorAll('.tab-link');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = document.getElementById(tab.dataset.tab);

            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            contents.forEach(c => c.classList.remove('active'));
            target.classList.add('active');
        });
    });

    // --- Help Modal logic ---
    const helpModal = document.getElementById('orderHelpModal');
    const closeHelpModal = document.getElementById('closeHelpModal');
    const helpBtns = document.querySelectorAll('.help-btn');
    const helpOrderIdSpan = document.getElementById('helpOrderId');

    helpBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const orderId = this.dataset.orderId;
            helpOrderIdSpan.textContent = orderId;
            helpModal.style.display = 'block';
        });
    });

    if (closeHelpModal) {
        closeHelpModal.onclick = () => helpModal.style.display = 'none';
    }
    
    window.addEventListener('click', function(event) {
        if (event.target == helpModal) {
            helpModal.style.display = 'none';
        }
    });
});