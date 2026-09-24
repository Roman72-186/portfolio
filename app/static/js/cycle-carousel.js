// Карусель циклов /cabinet/learning (владелец 24.09.2026, Этапы).
// Свайп — нативный CSS scroll-snap; единственная задача JS — открыть карусель
// на активной карточке, а не на первой слева.
document.addEventListener('DOMContentLoaded', function () {
    var active = document.querySelector('.lrn-carousel-card.is-current');
    if (active) {
        active.scrollIntoView({ inline: 'center', block: 'nearest' });
    }
});
