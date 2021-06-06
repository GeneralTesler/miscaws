message="changes"

push:
	git add .
	git commit -a -m $(message)
	git push origin master

format:
	poetry run black -l 120 simclient/

test:
	poetry run unittest discover -v

update: format test push

