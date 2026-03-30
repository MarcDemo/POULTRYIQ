document.addEventListener("DOMContentLoaded", function(){

let buttons = document.querySelectorAll(".edit-btn");

buttons.forEach(btn => {

    btn.addEventListener("click", function(){

        document.getElementById("edit_user_id").value = this.dataset.id;
        document.getElementById("edit_username").value = this.dataset.username;
        document.getElementById("edit_role").value = this.dataset.role;

    });

});

});