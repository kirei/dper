# $Id: Makefile 13051 2012-10-25 08:32:23Z jakob $

# fetch trang from http://code.google.com/p/jing-trang/
TRANG=	 	java -jar trang.jar

# xmllint is part of Libxml2, http://www.xmlsoft.org/
XMLLINT=	xmllint


SOURCE=		dper.pl
XML=		example.xml
RNC=		dper.rnc
RNG=		dper.rng
XSD=		dper.xsd

FILES=		$(SOURCE) $(RNC) $(XML)


.SUFFIXES: .rnc .rng .xsd

all: rng regress

clean:
	rm -f $(RNG) $(XSD)

rng: $(RNG)

xsd: $(XSD)

regress: $(RNG)
	${XMLLINT} --noout --relaxng $(RNG) $(XML)

.rnc.rng:
	${TRANG} $< $@

.rnc.xsd:
	${TRANG} $< $@
