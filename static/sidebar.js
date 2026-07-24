document.addEventListener("DOMContentLoaded", function () {
    // Procura pela barra lateral (mude para .sidebar-nav se esse for o nome da sua classe)
    const sidebar = document.querySelector(".sidebar"); 

    if (sidebar) {
        // Recupera a última posição salva no navegador e aplica na barra lateral
        const scrollPosition = localStorage.getItem("sidebar-scroll");
        if (scrollPosition) {
            sidebar.scrollTop = parseInt(scrollPosition, 10);
        }

        // Fica de olho na rolagem e salva a posição atual em tempo real
        sidebar.addEventListener("scroll", function () {
            localStorage.setItem("sidebar-scroll", sidebar.scrollTop);
        });
    }
});