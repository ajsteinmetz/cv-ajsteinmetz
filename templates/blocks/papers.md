<# profile README "Code for Papers": every publication with a repo, in publications.yml order #>
<% for p in pubs_all if p.repo is defined %>
- [<< p.repo >>](https://github.com/ajsteinmetz/<< p.repo >>): << r.sentences([r.text(p.title)]) >> << r.pub_short_venue(p) >>.
<% endfor %>
