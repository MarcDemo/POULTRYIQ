document.addEventListener("DOMContentLoaded", function(){

let buttons = document.querySelectorAll(".edit-btn");

buttons.forEach(btn => {

    btn.addEventListener("click", function(){

        let id = this.getAttribute("data-id");
        let eggs = this.getAttribute("data-eggs");

        document.getElementById("record_id").value = id;
        document.getElementById("edit_eggs").value = eggs;

    });

});

});