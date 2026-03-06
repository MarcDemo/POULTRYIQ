let quantity = document.getElementById("quantity")
let price = document.getElementById("price")
let total = document.getElementById("total")
let deposit = document.getElementById("deposit")
let balance = document.getElementById("balance")

function calculateTotal(){

let t = quantity.value * price.value
total.value = t

calculateBalance()
}

function calculateBalance(){

let b = total.value - deposit.value
balance.value = b

}

quantity.addEventListener("input", calculateTotal)
price.addEventListener("input", calculateTotal)
deposit.addEventListener("input", calculateBalance)