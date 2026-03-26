document.addEventListener("DOMContentLoaded", function(){

let quantity = document.getElementById("quantity");
let price = document.getElementById("price");
let total = document.getElementById("total");
let deposit = document.getElementById("deposit");
let balance = document.getElementById("balance");

function calculateTotal(){

let qty = parseFloat(quantity.value) || 0;
let pr = parseFloat(price.value) || 0;

let t = qty * pr;

total.value = t.toFixed(0);

calculateBalance();
}

function calculateBalance(){

let t = parseFloat(total.value) || 0;
let dep = parseFloat(deposit.value) || 0;

balance.value = (t - dep).toFixed(0);
}

quantity.addEventListener("input", calculateTotal);
price.addEventListener("input", calculateTotal);
deposit.addEventListener("input", calculateBalance);

});