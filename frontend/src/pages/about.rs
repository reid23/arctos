use crate::Route;
use dioxus::prelude::*;

#[component]
pub fn About() -> Element {
    rsx! {
        div { class: "row",
            div { class: "col-lg-8 mx-auto",
                h1 { "About Arctos" }
                p {
                    "Arctos, latin for "
                    i { "bear" }
                    ", is a "
                    a { href: "https://en.wikipedia.org/wiki/Recursive_acronym", "recursive" }
                    " "
                    a { href: "https://en.wikipedia.org/wiki/Backronym", "backronym" }
                    " that stands for "
                    strong { "A" }
                    "rctos: "
                    strong { "R" }
                    "eid's "
                    strong { "C" }
                    "omprehensive "
                    strong { "T" }
                    "ournament "
                    strong { "O" }
                    "rganization "
                    strong { "S" }
                    "ystem."
                }
                p {
                    "It is a tool designed by TOs, for TOs, with the goal of reducing the tournament organization workload. The design philosophy has three main components:"
                }
                ul {
                    li { strong { "capable & accessible" } ": give all juggers easy access to the best available tools and data." }
                    li { strong { "unopinionated" } ": don't impose unnecessary structure onto how tournaments are organized." }
                    li { strong { "minimal" } ": collect all data necessary for functionality and nothing more." }
                }
                div { class: "alert alert-info mt-5",
                    i { class: "fas fa-book" }
                    " Read more in the "
                    Link { to: Route::Docs {}, class: "alert-link", "User Documentation" }
                    "."
                }
                h2 { class: "mt-4", "Functionality" }
                div { class: "row mt-4",
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-calendar-alt text-primary" } " Flexible Scheduling" }
                                p { class: "card-text text-muted", "Give teams clear, predictable, and efficient schedules. Implement any tournament structure." }
                            }
                        }
                    }
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-bolt text-warning" } " Live Updates" }
                                p { class: "card-text text-muted", "Get live score updates for matches as they happen, and overlay a scoreboard on your live stream in OBS." }
                            }
                        }
                    }
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-magnifying-glass text-success" } " Player Tracking" }
                                p { class: "card-text text-muted", "Log injuries and view results for all tournaments." }
                            }
                        }
                    }
                }
                div { class: "row mt-2",
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-users text-info" } " Registration" }
                                p { class: "card-text text-muted", "Manage team and player registrations and payment. Stripe payment portal coming soon." }
                            }
                        }
                    }
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-wrench text-secondary" } " Head Ref Tools" }
                                p { class: "card-text text-muted", "Automatically track point outcomes, scores, and stone count. Log notes on teams, players, and points." }
                            }
                        }
                    }
                    div { class: "col-md-4 mb-3",
                        div { class: "card h-100 border-0 shadow-sm",
                            div { class: "card-body",
                                h5 { class: "card-title", i { class: "fas fa-list-ol text-danger" } " Side Comp Managment" }
                                p { class: "card-text text-muted", "Registraion and live results for several common individual competitions (bear pit, chain pit)." }
                            }
                        }
                    }
                }
                p {
                    "and more, that i'm too lazy to list here nicely! Again, see the "
                    Link { to: Route::Docs {}, class: "alert-link", "User Documentation" }
                    " for more details."
                }
            }
        }
    }
}
