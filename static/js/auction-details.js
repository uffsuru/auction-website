document.addEventListener('DOMContentLoaded', function() {
    const bidForm = document.getElementById('bidForm');
    const auctionId = bidForm ? bidForm.dataset.auctionId : null;
    const socket = io(); // Connect to Socket.IO
    
    function showAlert(message, type) {
        const successAlert = document.getElementById('successAlert');
        const errorAlert = document.getElementById('errorAlert');
        
        // Hide both alerts first
        successAlert.style.display = 'none';
        errorAlert.style.display = 'none';
        
        const alertToShow = type === 'success' ? successAlert : errorAlert;
        alertToShow.textContent = message;
        alertToShow.style.display = 'block';
        
        setTimeout(() => {
            alertToShow.style.display = 'none';
        }, 5000);
    }
    
    if (bidForm) {
        bidForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const bidAmountInput = document.getElementById('bidAmount');
            const bidAmount = bidAmountInput.value;
            const submitButton = this.querySelector('button[type="submit"]');
            
            if (!bidAmount || parseFloat(bidAmount) <= 0) {
                showAlert('Please enter a valid bid amount', 'error');
                return;
            }
            
            // Disable button during request
            submitButton.disabled = true;
            submitButton.textContent = 'Placing Bid...';
            
            try {
                const response = await fetch('/api/bid', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        auction_id: parseInt(auctionId), 
                        amount: parseFloat(bidAmount)
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    showAlert('Bid placed successfully!', 'success');
                    
                    // Update the current price display
                    document.getElementById('currentPrice').textContent = `₹${parseFloat(bidAmount).toFixed(2)}`;
                    
                    // Update minimum bid amount and clear input
                    bidAmountInput.min = parseFloat(bidAmount) + 0.01;
                    bidAmountInput.value = '';
                    
                    // Add new bid to history (using data from the page)
                    const bidList = document.getElementById('bidList');
                    const newBidItem = document.createElement('div');
                    newBidItem.className = 'bid-item';
                    
                    // This is a simplification. A full implementation would get the user name from a global JS variable or another API call.
                    // For now, we use "You" as a placeholder.
                    newBidItem.innerHTML = `
                        <span class="bidder">You</span>
                        <span class="bid-amount">₹${parseFloat(bidAmount).toFixed(2)}</span>
                        <span class="bid-time">Just now</span>
                    `;
                    
                    // If "No bids yet" message exists, remove it
                    const noBidsMessage = bidList.querySelector('div[style*="text-align: center"]');
                    if (noBidsMessage) {
                        noBidsMessage.remove();
                    }
                    
                    bidList.prepend(newBidItem);
                    
                } else {
                    showAlert(result.message, 'error');
                }
            } catch (error) {
                console.error('Error placing bid:', error);
                showAlert('An error occurred. Please try again.', 'error');
            } finally {
                // Re-enable button
                submitButton.disabled = false;
                submitButton.textContent = 'Place Bid';
            }
        });
    }

    // Listen for real-time bid updates from other users
    if (auctionId) {
        socket.on('new_bid', function(data) {
            // Check if the update is for the current auction
            if (data.auction_id == auctionId) {
                console.log('New bid received:', data);

                // Update the current price display
                document.getElementById('currentPrice').textContent = `₹${data.amount}`;
                
                // Update minimum bid amount on the input
                const bidAmountInput = document.getElementById('bidAmount');
                if (bidAmountInput) {
                    bidAmountInput.min = parseFloat(data.amount) + 0.01;
                }

                // Add new bid to the top of the history list
                const bidList = document.getElementById('bidList');
                if (bidList) {
                    const newBidItem = document.createElement('div');
                    newBidItem.className = 'bid-item';
                    
                    newBidItem.innerHTML = `
                        <span class="bidder">${data.user_name}</span>
                        <span class="bid-amount">₹${data.amount}</span>
                        <span class="bid-time">${data.bid_time}</span>
                    `;
                    
                    // If "No bids yet" message exists, remove it
                    const noBidsMessage = bidList.querySelector('div[style*="text-align: center"]');
                    if (noBidsMessage) { noBidsMessage.remove(); }
                    
                    bidList.prepend(newBidItem);
                }
            }
        });
    }
});