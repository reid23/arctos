IMAGE   := arctos-sphinx
ROOTDIR := $(shell git -C .. rev-parse --show-toplevel)
DOCKER  := docker run --rm --user $(shell id -u):$(shell id -g) -v $(ROOTDIR):/project -w /project $(IMAGE)

.PHONY: html clean image

image:
	docker build -t $(IMAGE) -f $(ROOTDIR)/build_system/Dockerfile $(ROOTDIR)

html: image
	$(DOCKER) sphinx-build --color -j auto -b html docs docs/_build/html
	@echo "Docs built: docs/_build/html/index.html"

clean:
	rm -rf _build
	@echo "Cleaned docs/_build"
