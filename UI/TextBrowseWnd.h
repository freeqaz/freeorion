// -*- C++ -*-
#ifndef _TextBrowseWnd_h_
#define _TextBrowseWnd_h_

#include <GG/GGFwd.h>
#include <GG/BrowseInfoWnd.h>


/** A popup tooltop for display when mousing over in-game icons.  A title and some detail text.*/
class TextBrowseWnd : public GG::BrowseInfoWnd {
public:
    TextBrowseWnd(const std::string& title_text, const std::string& main_text);
    virtual bool WndHasBrowseInfo(const Wnd* wnd, std::size_t mode) const;
    virtual void Render();

private:
    GG::Label*          m_title_text;
    GG::Label*          m_main_text;
    GG::Pt              m_offset;
};

#endif
