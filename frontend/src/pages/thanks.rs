use dioxus::prelude::*;
use reqwest::Client;

#[derive(Clone, Debug, PartialEq, serde::Deserialize)]
struct GithubContributor {
    login: String,
    html_url: String,
    avatar_url: String,
}

async fn load_contributors() -> Result<Vec<GithubContributor>, String> {
    Client::new()
        .get("https://api.github.com/repos/reid23/arctos/contributors")
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json::<Vec<GithubContributor>>()
        .await
        .map_err(|e| e.to_string())
}

#[component]
pub fn Thanks() -> Element {
    let mut contributors = use_resource(load_contributors);
    let contributors_val = contributors.value();

    rsx! {
        div { class: "row",
              div { class: "col-lg-6 mx-auto",
		    h1 { "Credits" }
		    p { "Arctos is an open-source project written by these wonderful people who donated their time:" }

                    if let Some(Ok(users)) = contributors_val.read().as_ref() {
                        if users.is_empty() {
                            p { class: "text-muted", "No contributors found." }
                        } else {
                            div { class: "d-flex flex-wrap gap-3",
                                  for user in users.iter() {
                                      a {
                                          key: "{user.login}",
                                          href: "{user.html_url}",
                                          class: "d-inline-flex align-items-center gap-2 text-decoration-none",
                                          target: "_blank",
                                          rel: "noreferrer",
                                          img {
                                              src: "{user.avatar_url}",
                                              width: "32",
                                              height: "32",
                                              style: "border-radius:50%;",
                                              alt: "{user.login}'s avatar",
                                          }
                                          span { "{user.login}" }
                                      }
                                  }
                            }
                        }
                    } else if let Some(Err(e)) = contributors_val.read().as_ref() {
                        div { role: "alert",
                            p { class: "text-muted", "Could not load contributors: {e}" }
                            button { r#type: "button", class: "btn btn-outline-secondary", onclick: move |_| contributors.restart(), "Try again" }
                        }
                    } else {
                        p { class: "text-muted", role: "status", "Loading contributors…" }
                    }

		    br {}
		    p {
			"Thanks to the creators of\n"
			    a { href: "https://github.com/twbs/bootstrap", rel: "nofollow", "bootstrap" }
			",\n"
			    a { href: "https://www.ffmpeg.org/", rel: "nofollow", "ffmpeg" }
			", "
			    a { href: "https://dioxuslabs.com", rel: "nofollow", "dioxus" }
			",\n"
			    a {
				href: "https://flask.palletsprojects.com/en/stable/",
				rel: "nofollow",
				"flask"
			    }
			",\n"
			    a { href: "https://sqlite.org/", rel: "nofollow", "sqlite" }
			", and so many more beautiful bits of\nsoftware that are used to make Arctos happen, for making wonderful\nsoftware and keeping it open for all to use."
		    }
		    p { "And thanks, chop0" }

		    
              }
        }
    }
}
